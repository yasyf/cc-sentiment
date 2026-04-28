from __future__ import annotations

from dataclasses import replace
from time import monotonic
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from textual import screen as t
from textual import work
from textual.app import ComposeResult
from textual.screen import Screen
from textual.widgets import Static

from cc_sentiment.models import (
    AppState,
    ClientConfig,
    ContributorId,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.onboarding import (
    Capabilities,
    InvalidTransition,
    SetupMachine,
    Stage,
    State,
)
from cc_sentiment.onboarding.discovery import (
    AutoVerify,
    GistDiscovery,
    IdentityProbe,
    LocalKeysProbe,
    Sanitizer,
)
from cc_sentiment.onboarding.events import (
    DiscoveryComplete,
    Event,
    GhAddFailed,
    GhAddVerified,
    GistTimedOut,
    GistVerified,
    NoSavedConfig,
    QuitOnboarding,
    ResumePendingEmail,
    ResumePendingGist,
    SavedConfigChecked,
    StartProcessing,
    VerificationOk,
    VerificationTimedOut,
    WorkingFailed,
    WorkingSucceeded,
)
from cc_sentiment.onboarding.persistence import Persistence
from cc_sentiment.onboarding.state import Identity, KeySource
from cc_sentiment.onboarding.state import GistTimeout, Trouble, VerifyTimeout
from cc_sentiment.onboarding.ui.screens import (
    BlockedScreen,
    DoneScreen,
    EmailScreen,
    GhAddScreen,
    GistTroubleScreen,
    InboxScreen,
    InitialScreen,
    KeyPickScreen,
    PublishScreen,
    SavedRetryScreen,
    SshMethodScreen,
    UserFormScreen,
    VerifyTroubleScreen,
    WelcomeScreen,
    WorkingScreen,
)
from cc_sentiment.signing import GPGBackend, KeyDiscovery, SSHBackend
from cc_sentiment.upload import (
    AuthOk,
    AuthUnauthorized,
    Uploader,
)


SCREEN_FACTORIES: dict[Stage, type] = {
    Stage.INITIAL: InitialScreen,
    Stage.SAVED_RETRY: SavedRetryScreen,
    Stage.WELCOME: WelcomeScreen,
    Stage.USER_FORM: UserFormScreen,
    Stage.KEY_PICK: KeyPickScreen,
    Stage.SSH_METHOD: SshMethodScreen,
    Stage.WORKING: WorkingScreen,
    Stage.PUBLISH: PublishScreen,
    Stage.GH_ADD: GhAddScreen,
    Stage.EMAIL: EmailScreen,
    Stage.INBOX: InboxScreen,
    Stage.BLOCKED: BlockedScreen,
    Stage.DONE: DoneScreen,
}


GIST_POLL_INTERVAL_AUTHED_SECONDS = 5.0
GIST_POLL_INTERVAL_UNAUTHED_SECONDS = 30.0
PROPAGATION_WINDOW_SECONDS = 600.0
VERIFY_POLL_INTERVAL_SECONDS = 10.0
MANAGED_RETRIES = 3


class OnboardingScreen(Screen[bool]):
    DEFAULT_CSS: ClassVar[str] = """
    OnboardingScreen { background: $background; }
    OnboardingScreen > Static#onboarding-bg { width: 100%; height: 100%; }
    """

    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        self.caps: Capabilities = Capabilities()
        self._verified_config: ClientConfig | None = None

    def compose(self) -> ComposeResult:
        yield Static("", id="onboarding-bg")

    def on_mount(self) -> None:
        self.run_worker(self._prefetch_caps(), name="caps-prefetch", exit_on_error=False)
        self._run()

    async def _prefetch_caps(self) -> None:
        await self.caps.has_ssh_keygen
        await self.caps.has_gpg
        await self.caps.has_gh
        await self.caps.has_brew
        await self.caps.gh_authenticated

    @work(group="orchestrator", exit_on_error=True)
    async def _run(self) -> None:
        state = self._initial_state()
        while True:
            view = self._build_view(state)
            event = await self._show(view, state)
            if isinstance(event, SavedConfigChecked) and event.result == "ok":
                self.dismiss(True)
                return
            if state.stage is Stage.DONE and isinstance(event, StartProcessing):
                await self._persist_done(state)
                self.dismiss(True)
                return
            if state.stage is Stage.BLOCKED and isinstance(event, QuitOnboarding):
                self.dismiss(False)
                return
            state = await self._apply_event(state, event)

    async def _show(self, view: t.Screen, state: State) -> Event:
        """Push view, await mount, launch side-effect, await dismiss result."""
        result: anyio.Event = anyio.Event()
        captured: list[Event] = []

        def on_dismissed(value: Event | None) -> None:
            if value is not None:
                captured.append(value)
            result.set()

        await self.app.push_screen(view, on_dismissed)
        self._launch_effect(state, view)
        await result.wait()
        return captured[0]

    def _initial_state(self) -> State:
        return State(
            stage=Stage.INITIAL,
            has_saved_config=self.app_state.config is not None,
        )

    def _build_view(self, state: State) -> t.Screen:
        if state.stage is Stage.TROUBLE:
            screen_cls = self._trouble_screen_for(state.trouble)
        else:
            screen_cls = SCREEN_FACTORIES[state.stage]
        if state.stage is Stage.SAVED_RETRY and self.app_state.config is not None:
            return SavedRetryScreen.with_config(self.app_state.config).render(state, self.caps)
        return screen_cls().render(state, self.caps)

    @staticmethod
    def _trouble_screen_for(trouble: Trouble | None) -> type:
        match trouble:
            case VerifyTimeout():
                return VerifyTroubleScreen
            case GistTimeout():
                return GistTroubleScreen
            case _:
                return GistTroubleScreen

    async def _apply_event(self, state: State, event: Event) -> State:
        if isinstance(event, DiscoveryComplete) and event.auto_verified_config is not None:
            self._verified_config = event.auto_verified_config
        if isinstance(event, StartProcessing):
            return state
        try:
            new_state = await SetupMachine.transition(state, event, self.caps)
        except InvalidTransition:
            return state
        if new_state.stage is Stage.DONE and self._verified_config is not None and new_state.verified_config is None:
            new_state = replace(new_state, verified_config=self._verified_config)
        return new_state

    async def _persist_done(self, state: State) -> None:
        if self._verified_config is not None:
            self.app_state.config = self._verified_config
        self.app_state.pending_setup = None
        await anyio.to_thread.run_sync(self.app_state.save)

    def _launch_effect(self, state: State, view: t.Screen) -> None:
        match state.stage:
            case Stage.INITIAL:
                coro = self._initial_effect(view)
            case Stage.WELCOME:
                coro = self._discovery_effect(state, view)
            case Stage.WORKING:
                coro = self._working_effect(state, view)
            case Stage.PUBLISH:
                coro = self._publish_effect(state, view)
            case Stage.GH_ADD:
                coro = self._gh_add_effect(state, view)
            case Stage.INBOX:
                coro = self._inbox_effect(state, view)
            case _:
                return
        self.run_worker(coro, group="effect", exclusive=True, exit_on_error=False)

    # ─── side-effects ───────────────────────────────────────────────────────

    async def _initial_effect(self, view: t.Screen) -> None:
        if self.app_state.pending_setup is not None:
            target = self.app_state.pending_setup.target
            if target == "email":
                view.dismiss(ResumePendingEmail())
            else:
                view.dismiss(ResumePendingGist())
            return
        if self.app_state.config is None:
            view.dismiss(NoSavedConfig())
            return
        match await Uploader().probe_credentials(self.app_state.config):
            case AuthOk():
                view.dismiss(SavedConfigChecked(result="ok"))
            case AuthUnauthorized():
                view.dismiss(SavedConfigChecked(result="invalid"))
            case _:
                view.dismiss(SavedConfigChecked(result="unreachable"))

    async def _discovery_effect(self, state: State, view: t.Screen) -> None:
        identity = await IdentityProbe.detect(self.app_state.github_username)
        if identity.github_username and not identity.email_usable:
            email, usable = await IdentityProbe.mine_email(identity.github_username)
            if email:
                identity = Identity(
                    github_username=identity.github_username,
                    email=email,
                    email_usable=usable,
                )
        existing = await LocalKeysProbe.detect_all()
        verified = await AutoVerify.probe(identity, existing)
        event = DiscoveryComplete(
            identity=identity,
            existing_keys=existing,
            auto_verified=verified is not None,
            auto_verified_config=verified,
        )
        view.discovery_done(event)

    async def _working_effect(self, state: State, view: t.Screen) -> None:
        username = state.identity.github_username
        s = WorkingScreen.strings()
        for attempt in range(MANAGED_RETRIES):
            try:
                view.set_status(s["creating_key"])
                info = await KeyDiscovery.generate_managed_ssh_key()
                pub_text = await SSHBackend(private_key_path=info.path).public_key_text()
                view.set_status(s["creating_gist"])
                gist_id = await KeyDiscovery.create_gist_from_text(pub_text)
                view.set_status(s["verifying"])
                config: ClientConfig = GistConfig(
                    contributor_id=ContributorId(username),
                    key_path=info.path,
                    gist_id=gist_id,
                )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    self._verified_config = config
                    view.dismiss(WorkingSucceeded())
                    return
            except (httpx.HTTPError, OSError, AssertionError) as exc:
                if attempt == MANAGED_RETRIES - 1:
                    Sanitizer.error(str(exc))
                    view.dismiss(WorkingFailed())
                    return
                await anyio.sleep(1.0)
        view.dismiss(WorkingFailed())

    async def _publish_effect(self, state: State, view: t.Screen) -> None:
        username = state.identity.github_username
        public_key = await self._public_key_for(state)
        if not username or not public_key:
            view.dismiss(GistTimedOut())
            return
        await self._persist_pending(state, target="gist")
        await self._poll_gist_until_verified(
            view,
            username=username,
            public_key=public_key,
            key_path=state.selected.key.path if state.selected and state.selected.key else None,
            gpg_fpr=(
                state.selected.key.fingerprint
                if state.selected and state.selected.key and state.selected.source is KeySource.EXISTING_GPG
                else None
            ),
        )

    async def _gh_add_effect(self, state: State, view: t.Screen) -> None:
        username = state.identity.github_username
        if (
            state.selected is None
            or state.selected.key is None
            or state.selected.key.path is None
            or not username
        ):
            view.dismiss(GhAddFailed())
            return
        key = state.selected.key
        assert key.path is not None
        assert self.caps is not None
        if self.caps.gh_authenticated:
            ok = await KeyDiscovery.upload_github_ssh_key(_ssh_key_info(key))
            if not ok:
                view.dismiss(GhAddFailed())
                return
            config: ClientConfig = SSHConfig(
                contributor_id=ContributorId(username),
                key_path=key.path,
            )
            if isinstance(await Uploader().probe_credentials(config), AuthOk):
                self._verified_config = config
                view.dismiss(GhAddVerified())
            else:
                view.dismiss(GhAddFailed())
            return
        await self._persist_pending(state, target="gh_add")
        await self._poll_gh_for_key_until_verified(
            view, username=username, key_path=key.path,
        )

    async def _inbox_effect(self, state: State, view: t.Screen) -> None:
        if state.selected is None or state.selected.key is None:
            view.dismiss(VerificationTimedOut(error_code="unknown"))
            return
        await self._persist_pending(state, target="email")
        config: ClientConfig = GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId(state.selected.key.fingerprint),
            fpr=state.selected.key.fingerprint,
        )
        await self._poll_credentials_until_verified(
            view, config=config,
        )

    # ─── helpers ────────────────────────────────────────────────────────────

    async def _public_key_for(self, state: State) -> str:
        if state.selected is None or state.selected.key is None:
            return ""
        key = state.selected.key
        if key.path is not None:
            return (await SSHBackend(private_key_path=key.path).public_key_text()).strip()
        return (await GPGBackend(fpr=key.fingerprint).public_key_text()).strip()

    async def _persist_pending(self, state: State, *, target) -> None:
        model = Persistence.from_state(state, target=target)
        self.app_state.pending_setup = model
        await anyio.to_thread.run_sync(self.app_state.save)

    async def _poll_gist_until_verified(
        self,
        view: t.Screen,
        *,
        username: str,
        public_key: str,
        key_path,
        gpg_fpr: str | None,
    ) -> None:
        assert self.caps is not None
        interval = (
            GIST_POLL_INTERVAL_AUTHED_SECONDS
            if self.caps.gh_authenticated
            else GIST_POLL_INTERVAL_UNAUTHED_SECONDS
        )
        started = monotonic()
        gist_seen = False
        while monotonic() - started < PROPAGATION_WINDOW_SECONDS:
            ref = await GistDiscovery.find_gist_with_public_key(username, public_key)
            if ref is not None:
                gist_seen = True
                config: ClientConfig
                if gpg_fpr:
                    config = GistGPGConfig(
                        contributor_id=ContributorId(ref.owner),
                        fpr=gpg_fpr,
                        gist_id=ref.gist_id,
                    )
                else:
                    assert key_path is not None
                    config = GistConfig(
                        contributor_id=ContributorId(ref.owner),
                        key_path=key_path,
                        gist_id=ref.gist_id,
                    )
                if isinstance(await Uploader().probe_credentials(config), AuthOk):
                    self._verified_config = config
                    view.dismiss(GistVerified())
                    return
            await anyio.sleep(interval)
        # Per plan: gist found but verify failed → restart-only VerifyTrouble;
        # gist never found → GistTrouble with username edit.
        view.dismiss(
            VerificationTimedOut(error_code="key-not-found") if gist_seen else GistTimedOut()
        )

    async def _poll_gh_for_key_until_verified(
        self,
        view: t.Screen,
        *,
        username: str,
        key_path,
    ) -> None:
        started = monotonic()
        config = SSHConfig(
            contributor_id=ContributorId(username),
            key_path=key_path,
        )
        while monotonic() - started < PROPAGATION_WINDOW_SECONDS:
            if isinstance(await Uploader().probe_credentials(config), AuthOk):
                self._verified_config = config
                view.dismiss(GhAddVerified())
                return
            await anyio.sleep(VERIFY_POLL_INTERVAL_SECONDS)
        view.dismiss(GhAddFailed())

    async def _poll_credentials_until_verified(
        self,
        view: t.Screen,
        *,
        config: ClientConfig,
    ) -> None:
        started = monotonic()
        while monotonic() - started < PROPAGATION_WINDOW_SECONDS:
            match await Uploader().probe_credentials(config):
                case AuthOk():
                    self._verified_config = config
                    view.dismiss(VerificationOk())
                    return
                case AuthUnauthorized():
                    pass
                case _:
                    pass
            await anyio.sleep(VERIFY_POLL_INTERVAL_SECONDS)
        view.dismiss(VerificationTimedOut(error_code="key-not-found"))


def _ssh_key_info(key):
    from cc_sentiment.signing import SSHKeyInfo
    return SSHKeyInfo(
        path=key.path,
        algorithm=key.algorithm or "ssh-ed25519",
        comment="cc-sentiment",
    )
