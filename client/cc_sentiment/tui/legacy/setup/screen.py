from __future__ import annotations

from contextlib import suppress
from time import monotonic
from typing import ClassVar

import anyio
import anyio.to_thread
from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import (
    Button,
    ContentSwitcher,
    Input,
    Static,
)
from textual.worker import Worker

from cc_sentiment.models import (
    AppState,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.signing import (
    GPGBackend,
    KeyDiscovery,
    SSHBackend,
)
from cc_sentiment.tui.popovers.dialog import Dialog
from cc_sentiment.tui.legacy.setup.alternate import AlternateStageMixin
from cc_sentiment.tui.legacy.setup.blocked import BlockedStageMixin
from cc_sentiment.tui.legacy.setup.done import DoneStageMixin
from cc_sentiment.tui.legacy.setup.pending import PendingLifecycleMixin
from cc_sentiment.tui.legacy.setup.publish import PublishStageMixin
from cc_sentiment.tui.legacy.setup.resume import ResumeMixin
from cc_sentiment.tui.legacy.setup.trouble import TroubleStageMixin
from cc_sentiment.tui.legacy.setup.verify import VerifyMixin
from cc_sentiment.tui.legacy.setup.welcome import WelcomeStageMixin
from cc_sentiment.tui.legacy.setup.working import WorkingStageMixin
from cc_sentiment.tui.legacy.setup_helpers import (
    DiscoveryRunner,
    IdentityProbe,
    SetupRoutePlanner,
)
from cc_sentiment.tui.legacy.setup_state import (
    DiscoveryResult,
    ExistingGPGKey,
    ExistingSSHKey,
    IdentityDiscovery,
    KeyKind,
    PendingSetup,
    PublishMethod,
    ResolvedGPGKey,
    ResolvedKey,
    ResolvedSSHKey,
    SetupAggregate,
    SetupIntervention,
    SetupRoute,
    SetupStage,
    Tone,
    UsernameSource,
    VerificationPollState,
)

__all__ = ["SetupScreen", "SetupStage"]

Config = SSHConfig | GPGConfig | GistConfig | GistGPGConfig


class SetupScreen(
    WelcomeStageMixin,
    AlternateStageMixin,
    WorkingStageMixin,
    PublishStageMixin,
    BlockedStageMixin,
    TroubleStageMixin,
    DoneStageMixin,
    PendingLifecycleMixin,
    ResumeMixin,
    VerifyMixin,
    Dialog[bool],
):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    SetupScreen > #dialog-box .status-line { width: 100%; min-height: 1; margin: 0 0 1 0; }
    SetupScreen > #dialog-box .copy { width: 100%; }
    SetupScreen > #dialog-box .publish-key-text { width: 100%; }
    SetupScreen > #dialog-box .publish-fallback { width: 100%; }
    SetupScreen > #dialog-box .welcome-actions,
    SetupScreen > #dialog-box .publish-actions,
    SetupScreen > #dialog-box .working-actions,
    SetupScreen > #dialog-box .alternate-actions,
    SetupScreen > #dialog-box .blocked-actions,
    SetupScreen > #dialog-box .trouble-actions { width: 100%; align-horizontal: center; }
    """

    BINDINGS = [
        Binding("enter", "activate_primary", "Continue", priority=True),
        Binding("escape", "cancel", "Quit", priority=True),
        Binding("ctrl+c", "cancel", "Quit", priority=True),
    ]

    PRIMARY_FOCUS_BY_STAGE: ClassVar[dict[SetupStage, str]] = {
        SetupStage.WELCOME: "#welcome-go",
        SetupStage.ALTERNATE: "#alternate-go",
        SetupStage.PUBLISH: "#publish-open",
        SetupStage.BLOCKED: "#blocked-install",
        SetupStage.TROUBLE: "#trouble-keep",
        SetupStage.DONE: "#done-btn",
    }

    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.aggregate = SetupAggregate(verification_poll=VerificationPollState(started_at=monotonic()))
        self.verify_worker: Worker[None] | None = None
        self.gist_watch_worker: Worker[None] | None = None
        self.github_lookup_allowed: bool = True

    @property
    def discovery(self) -> DiscoveryResult:
        return self.aggregate.discovery

    @property
    def selected_route(self) -> SetupRoute | None:
        return self.aggregate.selected_route

    @selected_route.setter
    def selected_route(self, value: SetupRoute | None) -> None:
        self.aggregate.selected_route = value

    @property
    def pending(self) -> PendingSetup | None:
        return self.aggregate.pending

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            with ContentSwitcher(initial=SetupStage.WELCOME.value):
                yield from self._compose_welcome()
                yield from self._compose_alternate()
                yield from self._compose_working()
                yield from self._compose_publish()
                yield from self._compose_blocked()
                yield from self._compose_trouble()
                yield from self._compose_done()

    @property
    def current_stage(self) -> SetupStage:
        return SetupStage(self.query_one(ContentSwitcher).current)

    def transition_to(self, stage: SetupStage) -> None:
        if self.current_stage is stage:
            self.call_after_refresh(self._focus_step_target, stage)
            return
        if (
            self.current_stage is SetupStage.PUBLISH
            and stage is not SetupStage.PUBLISH
            and self.gist_watch_worker is not None
        ):
            self.gist_watch_worker.cancel()
            self.gist_watch_worker = None
        self.query_one(ContentSwitcher).current = stage.value
        self.call_after_refresh(self._focus_step_target, stage)

    def on_mount(self) -> None:
        self._welcome_on_mount()
        self._alternate_on_mount()
        self._working_on_mount()
        self._publish_on_mount()
        self._blocked_on_mount()
        self.set_interval(0.5, self._poll_due)
        self.start_setup()

    def on_unmount(self) -> None:
        if self.verify_worker is not None:
            self.verify_worker.cancel()
        if self.aggregate.working.worker is not None:
            self.aggregate.working.worker.cancel()
        if self.gist_watch_worker is not None:
            self.gist_watch_worker.cancel()

    def action_activate_primary(self) -> None:
        with suppress(NoMatches, StopIteration):
            next(self.query(f"#{self.current_stage.value} Button.-primary").results(Button)).press()

    def action_cancel(self) -> None:
        self.dismiss(False)

    def _focus_widget(self, widget: Input | Button) -> None:
        if not widget.display or widget.disabled:
            return
        widget.focus()

    def _focus_step_target(self, stage: SetupStage) -> None:
        if (selector := self.PRIMARY_FOCUS_BY_STAGE.get(stage)) is None:
            return
        with suppress(NoMatches):
            target = self.query_one(selector, Button)
            self._focus_widget(target)

    @work()
    async def start_setup(self) -> None:
        if await self._maybe_resume_pending():
            return
        match await self._verify_saved_state():
            case "ok" | "temporary":
                return
            case "none" | "invalid":
                pass
        self._show_welcome_busy(True)
        await self._run_discover_phase()

    async def start_setup_flow(self: "SetupScreen") -> None:
        await self._run_discover_phase()

    async def _run_discover_phase(self) -> None:
        username_hint = self._best_known_username()
        result = await anyio.to_thread.run_sync(
            DiscoveryRunner.run, username_hint, self.github_lookup_allowed,
        )
        self.aggregate.discovery = result
        if (verified := await self._auto_verify(result)) is not None:
            self.state.config = verified
            await anyio.to_thread.run_sync(self.state.save)
            self._enter_settings_for_saved_config()
            return
        await self._silent_replan()

    async def _silent_replan(self: "SetupScreen") -> None:
        result = self.discovery
        match result.plan.intervention:
            case SetupIntervention.USERNAME:
                self._show_inline_username_prompt()
                return
            case SetupIntervention.BLOCKED:
                self._render_blocked(result)
                self.transition_to(SetupStage.BLOCKED)
                return
            case SetupIntervention.NONE:
                pass
        if (route := result.plan.recommended) is None:
            self._render_blocked(result)
            self.transition_to(SetupStage.BLOCKED)
            return
        self.selected_route = route
        match route.publish_method:
            case PublishMethod.GIST_AUTO:
                await self._enter_working(route)
            case PublishMethod.GIST_MANUAL:
                await self._enter_publish(route)
            case PublishMethod.OPENPGP:
                await self._enter_alternate()

    def _best_known_username(self) -> str:
        if self.state.github_username:
            return self.state.github_username
        match self.state.config:
            case SSHConfig(contributor_id=cid) | GistConfig(contributor_id=cid) | GistGPGConfig(contributor_id=cid):
                return cid
            case GPGConfig(contributor_type="github", contributor_id=cid):
                return cid
            case _:
                pass
        if self.state.pending_setup is not None and self.state.pending_setup.username:
            return self.state.pending_setup.username
        return ""

    async def _set_username(self, username: str, source: UsernameSource) -> None:
        existing = self.discovery.identity
        new_identity = IdentityDiscovery(
            github_username=username,
            username_source=source,
            github_email=existing.github_email,
            email_source=existing.email_source,
            email_usable=existing.email_usable,
        )
        if username and not new_identity.email_usable:
            email, src, usable = await anyio.to_thread.run_sync(IdentityProbe.mine_email, username)
            new_identity = IdentityDiscovery(
                github_username=username,
                username_source=source,
                github_email=email,
                email_source=src,
                email_usable=usable,
            )
        plan = SetupRoutePlanner.plan(
            self.discovery.capabilities,
            new_identity,
            github_lookup_allowed=self.github_lookup_allowed,
        )
        self.aggregate.discovery = DiscoveryResult(
            capabilities=self.discovery.capabilities,
            identity=new_identity,
            existing_ssh=self.discovery.existing_ssh,
            existing_gpg=self.discovery.existing_gpg,
            plan=plan,
        )

    def _resolve_key(self, route: SetupRoute) -> ResolvedKey:
        if self.aggregate.resolved_key is not None:
            return self.aggregate.resolved_key
        match route.key_plan:
            case ExistingSSHKey(info=info, managed=managed):
                resolved: ResolvedKey = ResolvedSSHKey(info=info, managed=managed)
            case ExistingGPGKey(info=info, managed=managed):
                resolved = ResolvedGPGKey(info=info, managed=managed)
            case _ if route.key_kind is KeyKind.SSH:
                resolved = ResolvedSSHKey(info=KeyDiscovery.generate_managed_ssh_key(), managed=True)
            case _ if route.key_kind is KeyKind.GPG:
                identity = self.discovery.identity
                resolved = ResolvedGPGKey(
                    info=KeyDiscovery.generate_managed_gpg_key(
                        identity.github_username or "cc-sentiment",
                        identity.github_email,
                    ),
                    managed=True,
                )
            case _:
                raise AssertionError("route has no key plan")
        self.aggregate.resolved_key = resolved
        return resolved

    @staticmethod
    def _public_key_text(resolved: ResolvedKey) -> str:
        match resolved:
            case ResolvedSSHKey(info=info):
                return SSHBackend(private_key_path=info.path).public_key_text()
            case ResolvedGPGKey(info=info):
                return GPGBackend(fpr=info.fpr).public_key_text()

    def _update_status(self, widget_id: str, text: str, tone: Tone = Tone.MUTED) -> None:
        with suppress(NoMatches):
            widget = self.query_one(f"#{widget_id}", Static)
            for member in Tone:
                widget.remove_class(member.value)
            widget.add_class(tone.value)
            widget.update(text)
