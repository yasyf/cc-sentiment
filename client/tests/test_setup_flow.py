from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App
from textual.widgets import Button, Input, Static

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistConfig,
    PendingSetupModel,
    SSHConfig,
)
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui.screens import SetupScreen
from cc_sentiment.tui.screens.setup.copy import USERNAME_ERROR_NOT_FOUND
from cc_sentiment.tui.setup_helpers import (
    GistDiscovery,
    GistMetadata,
    GistRef,
    SetupRoutePlanner,
)
from cc_sentiment.tui.setup_state import (
    DiscoveryResult,
    ExistingGPGKey,
    ExistingSSHKey,
    GenerateGPGKey,
    GenerateSSHKey,
    IdentityDiscovery,
    KeyKind,
    PublishMethod,
    RouteId,
    SetupIntervention,
    SetupPlan,
    SetupRoute,
    SetupStage,
    ToolCapabilities,
)
from cc_sentiment.tui.system import Clipboard
from cc_sentiment.tui.widgets.link_row import LinkRow
from cc_sentiment.upload import (
    AuthOk,
    AuthUnauthorized,
    AuthUnreachable,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def capabilities(**overrides) -> ToolCapabilities:
    return ToolCapabilities(**overrides)


def identity(
    username: str = "",
    email: str = "",
    email_usable: bool = False,
) -> IdentityDiscovery:
    return IdentityDiscovery(
        github_username=username,
        github_email=email,
        email_usable=email_usable,
    )


def write_ssh_keypair(tmp_path: Path, name: str = "id_ed25519") -> Path:
    key_path = tmp_path / name
    key_path.write_text("private")
    key_path.with_suffix(".pub").write_text("ssh-ed25519 AAAA cc-sentiment")
    return key_path


def make_ssh_info(tmp_path: Path, name: str = "id_ed25519") -> SSHKeyInfo:
    write_ssh_keypair(tmp_path, name)
    return SSHKeyInfo(
        path=tmp_path / name,
        algorithm="ssh-ed25519",
        comment="cc-sentiment",
    )


def make_existing_ssh(tmp_path: Path, *, managed: bool = False) -> ExistingSSHKey:
    return ExistingSSHKey(info=make_ssh_info(tmp_path), managed=managed)


def make_existing_gpg(
    fpr: str = "DEADBEEFCAFE0001",
    email: str = "alice@example.com",
    *,
    managed: bool = False,
) -> ExistingGPGKey:
    return ExistingGPGKey(
        info=GPGKeyInfo(fpr=fpr, email=email, algo="ed25519"),
        managed=managed,
    )


def gist_metadata(owner: str, gist_id: str, public_key: str) -> GistMetadata:
    return GistMetadata(
        ref=GistRef(owner=owner, gist_id=gist_id),
        description="cc-sentiment public key",
        file_contents={"cc-sentiment.pub": public_key},
    )


def route_managed_ssh_gist() -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.MANAGED_SSH_GIST,
        publish_method=PublishMethod.GIST_AUTO,
        key_kind=KeyKind.SSH,
        key_plan=GenerateSSHKey(),
    )


def route_managed_ssh_manual_gist() -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.MANAGED_SSH_MANUAL_GIST,
        publish_method=PublishMethod.GIST_MANUAL,
        key_kind=KeyKind.SSH,
        key_plan=GenerateSSHKey(),
    )


def route_managed_gpg_email() -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.MANAGED_GPG_OPENPGP,
        publish_method=PublishMethod.OPENPGP,
        key_kind=KeyKind.GPG,
        key_plan=GenerateGPGKey(),
    )


def pending_ssh_gist(
    tmp_path: Path,
    *,
    username: str = "alice",
    publish_method: str = "gist-manual",
    route_id: str = "managed-ssh-manual-gist",
) -> PendingSetupModel:
    return PendingSetupModel(
        route_id=route_id,
        publish_method=publish_method,
        key_kind="ssh",
        key_managed=True,
        key_path=write_ssh_keypair(tmp_path),
        username=username,
    )


def pending_gpg_email_sent(
    fpr: str = "DEADBEEFCAFE0001",
    email: str = "alice@example.com",
) -> PendingSetupModel:
    return PendingSetupModel(
        route_id="managed-gpg-openpgp",
        publish_method="openpgp",
        key_kind="gpg",
        key_managed=True,
        key_fpr=fpr,
        email=email,
        public_location="keys.openpgp.org",
        last_status="openpgp-email-sent",
    )


# ---------------------------------------------------------------------------
# Test harness
# ---------------------------------------------------------------------------


class SetupHarness(App[None]):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(SetupScreen(self.state), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


async def wait_for_stage(pilot, stage: SetupStage):
    screen = pilot.app.screen
    for _ in range(40):
        await pilot.pause(delay=0.1)
        if getattr(screen, "current_stage", None) is stage:
            return screen
    return screen


async def wait_until(pilot, predicate, *, attempts: int = 40, delay: float = 0.1) -> bool:
    for _ in range(attempts):
        await pilot.pause(delay=delay)
        if predicate():
            return True
    return False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stub_discovery(monkeypatch):
    discoveries: list[DiscoveryResult] = []

    def push(result: DiscoveryResult) -> None:
        discoveries.append(result)

    def fake_run(saved_username: str = "", github_lookup_allowed: bool = True) -> DiscoveryResult:
        return discoveries.pop(0) if discoveries else DiscoveryResult()

    monkeypatch.setattr(
        "cc_sentiment.tui.setup_helpers.DiscoveryRunner.run", staticmethod(fake_run),
    )
    return push


@pytest.fixture(autouse=True)
def isolated_state_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        AppState,
        "state_path",
        classmethod(lambda _cls: tmp_path / "state.json"),
    )


@pytest.fixture(autouse=True)
def no_browser(monkeypatch):
    monkeypatch.setattr("cc_sentiment.tui.system.Browser.open", lambda _url: True)


@pytest.fixture
def auth_ok():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        yield


@pytest.fixture
def auth_unauthorized():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnauthorized(status=401),
    ):
        yield


@pytest.fixture
def auth_unreachable():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnreachable(detail="dns failure"),
    ):
        yield


# ===========================================================================
# Pure planner — Welcome decision logic
# ===========================================================================


class TestPlannerFromWelcome:
    """One test per outgoing edge from the Welcome state in the decision tree."""

    def test_no_ssh_no_gpg_returns_blocked(self):
        plan = SetupRoutePlanner.plan(capabilities(), identity("alice"))
        assert plan.intervention is SetupIntervention.BLOCKED
        assert plan.recommended is None

    def test_no_github_with_no_gpg_returns_blocked(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True),
            identity("alice"),
            github_lookup_allowed=False,
        )
        assert plan.intervention is SetupIntervention.BLOCKED

    def test_gh_authenticated_recommends_managed_ssh_gist(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity("alice"),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_SSH_GIST
        assert plan.recommended.publish_method is PublishMethod.GIST_AUTO
        assert plan.recommended.key_kind is KeyKind.SSH
        assert isinstance(plan.recommended.key_plan, GenerateSSHKey)

    def test_username_present_no_gh_recommends_manual_gist(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True),
            identity("alice"),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_SSH_MANUAL_GIST
        assert plan.recommended.publish_method is PublishMethod.GIST_MANUAL

    def test_no_username_with_ssh_and_gpg_recommends_email(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True, has_gpg=True),
            identity(),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_GPG_OPENPGP
        assert plan.recommended.publish_method is PublishMethod.OPENPGP

    def test_gpg_only_no_ssh_keygen_recommends_email(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gpg=True),
            identity(),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_GPG_OPENPGP

    def test_ssh_only_no_username_no_gpg_prompts_for_username(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True),
            identity(),
        )
        assert plan.intervention is SetupIntervention.USERNAME

    def test_no_github_link_with_gpg_recommends_email(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True, has_gpg=True),
            identity("alice"),
            github_lookup_allowed=False,
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_GPG_OPENPGP


class TestPlannerWithExistingKeys:
    def test_existing_ssh_key_routes_to_key_pick(self, tmp_path: Path):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True, has_gh=True, gh_authed=True),
            identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
        )
        assert plan.intervention is SetupIntervention.KEY_PICK

    def test_existing_gpg_with_email_routes_to_key_pick(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gpg=True),
            identity(),
            existing_gpg=(make_existing_gpg(),),
        )
        assert plan.intervention is SetupIntervention.KEY_PICK

    def test_existing_gpg_without_email_omitted_falls_to_managed(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gpg=True),
            identity(),
            existing_gpg=(make_existing_gpg(email=""),),
        )
        assert plan.intervention is not SetupIntervention.KEY_PICK
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_GPG_OPENPGP


# ===========================================================================
# Persistence: PendingSetupModel
# ===========================================================================


class TestPendingSetupModel:
    def test_extra_fields_rejected(self, tmp_path: Path):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-ssh-gist",
                publish_method="gist-auto",
                key_kind="ssh",
                key_managed=True,
                key_path=tmp_path / "k",
                username="alice",
                location="GitHub gist",
            )

    def test_ssh_route_requires_key_path(self):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-ssh-gist",
                publish_method="gist-auto",
                key_kind="ssh",
                key_managed=True,
            )

    def test_gpg_route_requires_key_fpr(self):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-gpg-openpgp",
                publish_method="openpgp",
                key_kind="gpg",
                key_managed=True,
            )

    def test_ssh_route_round_trips(self, tmp_path: Path):
        AppState(pending_setup=pending_ssh_gist(tmp_path)).save()
        loaded = AppState.load()
        assert loaded.pending_setup is not None
        assert loaded.pending_setup.key_kind == "ssh"
        assert loaded.pending_setup.publish_method == "gist-manual"

    def test_gpg_email_route_round_trips(self):
        AppState(pending_setup=pending_gpg_email_sent()).save()
        loaded = AppState.load()
        assert loaded.pending_setup is not None
        assert loaded.pending_setup.key_kind == "gpg"
        assert loaded.pending_setup.publish_method == "openpgp"


# ===========================================================================
# System helpers: Clipboard, Gist discovery
# ===========================================================================


class TestClipboardCommand:
    def test_returns_argv_on_known_platform(self, monkeypatch):
        monkeypatch.setattr("cc_sentiment.tui.system.sys.platform", "linux")
        monkeypatch.setattr(
            "cc_sentiment.tui.system.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None,
        )
        assert Clipboard.command() == ["wl-copy"]

    def test_returns_none_when_nothing_present(self, monkeypatch):
        monkeypatch.setattr("cc_sentiment.tui.system.sys.platform", "linux")
        monkeypatch.setattr("cc_sentiment.tui.system.shutil.which", lambda _name: None)
        assert Clipboard.command() is None


class TestGistDiscovery:
    def test_find_gist_matches_by_content_in_any_file(self, monkeypatch):
        public_key = "ssh-ed25519 AAAA cc-sentiment"
        monkeypatch.setattr(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.list_public_gists",
            classmethod(lambda _cls, username, limit=10: (
                GistRef(owner=username, gist_id="aaaaaaaaaaaaaaaaaaaa"),
                GistRef(owner=username, gist_id="bbbbbbbbbbbbbbbbbbbb"),
            )),
        )

        def fake_metadata(_cls, ref: GistRef) -> GistMetadata:
            files = (
                {"random.txt": "unrelated", "key.pub": public_key}
                if ref.gist_id.startswith("b")
                else {"random.txt": "unrelated"}
            )
            return GistMetadata(ref=ref, description="anything", file_contents=files)

        monkeypatch.setattr(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            classmethod(fake_metadata),
        )
        ref = GistDiscovery.find_gist_with_public_key("alice", public_key)
        assert ref is not None
        assert ref.gist_id == "bbbbbbbbbbbbbbbbbbbb"

    def test_find_gist_returns_none_when_no_match(self, monkeypatch):
        monkeypatch.setattr(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.list_public_gists",
            classmethod(lambda _cls, username, limit=10: (
                GistRef(owner=username, gist_id="aaaaaaaaaaaaaaaaaaaa"),
            )),
        )
        monkeypatch.setattr(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            classmethod(lambda _cls, ref: GistMetadata(
                ref=ref, description="x", file_contents={"random.txt": "unrelated"},
            )),
        )
        assert GistDiscovery.find_gist_with_public_key("alice", "ssh-ed25519 AAAA") is None


# ===========================================================================
# Screen: Saved → Done | SavedRetry | Welcome | Publish | Inbox
# ===========================================================================


class TestSavedTransitions:
    async def test_valid_lands_on_done(
        self, tmp_path: Path, auth_ok, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.DONE)
            assert screen.current_stage is SetupStage.DONE

    async def test_network_unreachable_lands_on_saved_retry(
        self, tmp_path: Path, auth_unreachable, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.SAVED_RETRY)
            assert screen.current_stage is SetupStage.SAVED_RETRY

    async def test_invalid_lands_on_welcome(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.WELCOME)
            assert screen.current_stage is SetupStage.WELCOME

    async def test_pending_gist_lands_on_publish(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            assert screen.current_stage is SetupStage.PUBLISH

    async def test_pending_email_sent_lands_on_inbox(
        self, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_gpg_email_sent())
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.INBOX)
            assert screen.current_stage is SetupStage.INBOX

    async def test_pending_with_missing_ssh_key_clears_pending(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(
            pending_setup=PendingSetupModel(
                route_id="managed-ssh-manual-gist",
                publish_method="gist-manual",
                key_kind="ssh",
                key_managed=True,
                key_path=tmp_path / "missing_id_ed25519",
                username="alice",
            ),
        )
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_ssh_manual_gist()),
        ))
        async with SetupHarness(state).run_test() as pilot:
            await wait_until(pilot, lambda: state.pending_setup is None, attempts=30)
        assert state.pending_setup is None


# ===========================================================================
# Screen: Welcome → KeyPick | Working | Publish | Email | UserForm | Blocked
# ===========================================================================


class TestWelcomeTransitions:
    async def test_no_path_routes_to_blocked(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(),
            identity=identity(),
            plan=SetupPlan(intervention=SetupIntervention.BLOCKED),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.BLOCKED)
            assert screen.current_stage is SetupStage.BLOCKED

    async def test_gh_managed_ssh_routes_to_working(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_ssh_gist()),
        ))
        seeded = make_ssh_info(tmp_path)
        with patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.create_gist_from_text",
            return_value="abc123def456",
        ), patch(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            return_value=gist_metadata(
                "alice", "abc123def456", "ssh-ed25519 AAAA cc-sentiment",
            ),
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.WORKING)
                assert screen.current_stage is SetupStage.WORKING

    async def test_user_no_gh_routes_to_publish(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_ssh_manual_gist()),
        ))
        seeded = make_ssh_info(tmp_path)
        with patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
                assert screen.current_stage is SetupStage.PUBLISH

    async def test_gpg_path_routes_to_email(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gpg=True),
            identity=identity(),
            plan=SetupPlan(route_managed_gpg_email()),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.EMAIL)
            assert screen.current_stage is SetupStage.EMAIL

    async def test_ssh_only_no_username_routes_to_user_form(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True),
            identity=identity(),
            plan=SetupPlan(intervention=SetupIntervention.USERNAME),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.USER_FORM)
            assert screen.current_stage is SetupStage.USER_FORM

    async def test_existing_keys_route_to_key_pick(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gh=True, gh_authed=True),
            identity=identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
            assert screen.current_stage is SetupStage.KEY_PICK


# ===========================================================================
# Screen: UserForm → Publish | Email | Blocked
# ===========================================================================


class TestUserFormTransitions:
    @pytest.fixture
    def discovery_username_intervention(self, stub_discovery):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True),
            identity=identity(),
            plan=SetupPlan(intervention=SetupIntervention.USERNAME),
        ))

    async def test_valid_username_routes_to_publish(
        self,
        tmp_path: Path,
        auth_unauthorized,
        discovery_username_intervention,
    ):
        seeded = make_ssh_info(tmp_path)
        with patch(
            "cc_sentiment.tui.setup_helpers.IdentityProbe.validate_username",
            return_value="ok",
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.USER_FORM)
                screen.query_one("#user-form-input", Input).value = "alice"
                screen.query_one("#user-form-go", Button).press()
                screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
                assert screen.current_stage is SetupStage.PUBLISH

    async def test_validation_error_stays_in_user_form(
        self, auth_unauthorized, discovery_username_intervention,
    ):
        with patch(
            "cc_sentiment.tui.setup_helpers.IdentityProbe.validate_username",
            return_value="not-found",
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.USER_FORM)
                screen.query_one("#user-form-input", Input).value = "ghost"
                screen.query_one("#user-form-go", Button).press()
                await wait_until(
                    pilot,
                    lambda: bool(
                        str(screen.query_one("#user-form-status", Static).render())
                    ),
                )
                status = str(screen.query_one("#user-form-status", Static).render())
                assert USERNAME_ERROR_NOT_FOUND.format(user="ghost") == status
                assert screen.current_stage is SetupStage.USER_FORM

    async def test_no_github_with_gpg_routes_to_email(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gpg=True),
            identity=identity(),
            plan=SetupPlan(intervention=SetupIntervention.USERNAME),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.USER_FORM)
            link = screen.query_one("#user-form-no-github", LinkRow)
            link.post_message(LinkRow.Pressed(link))
            screen = await wait_for_stage(pilot, SetupStage.EMAIL)
            assert screen.current_stage is SetupStage.EMAIL
            assert screen.github_lookup_allowed is False

    async def test_no_github_without_gpg_routes_to_blocked(
        self, auth_unauthorized, discovery_username_intervention,
    ):
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.USER_FORM)
            link = screen.query_one("#user-form-no-github", LinkRow)
            link.post_message(LinkRow.Pressed(link))
            screen = await wait_for_stage(pilot, SetupStage.BLOCKED)
            assert screen.current_stage is SetupStage.BLOCKED


# ===========================================================================
# Screen: KeyPick → SshMethod | Email | Working | Publish
# ===========================================================================


class TestKeyPickTransitions:
    async def test_existing_ssh_choice_routes_to_ssh_method(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gh=True, gh_authed=True),
            identity=identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
            screen.query_one("#key-pick-existing-0", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.SSH_METHOD)
            assert screen.current_stage is SetupStage.SSH_METHOD

    async def test_existing_gpg_choice_routes_to_email(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gpg=True),
            identity=identity(),
            existing_gpg=(make_existing_gpg(),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
            screen.query_one("#key-pick-existing-0", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.EMAIL)
            assert screen.current_stage is SetupStage.EMAIL

    async def test_managed_choice_with_gh_routes_to_working(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity=identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        seeded = make_ssh_info(tmp_path, name="managed_id_ed25519")
        with patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.create_gist_from_text",
            return_value="abc123",
        ), patch(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            return_value=gist_metadata(
                "alice", "abc123", "ssh-ed25519 AAAA cc-sentiment",
            ),
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
                screen.query_one("#key-pick-managed", Button).press()
                screen = await wait_for_stage(pilot, SetupStage.WORKING)
                assert screen.current_stage is SetupStage.WORKING


# ===========================================================================
# Screen: SshMethod → Publish | GhAdd
# ===========================================================================


class TestSshMethodTransitions:
    async def test_default_choice_routes_to_publish(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gh=True, gh_authed=True),
            identity=identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
            screen.query_one("#key-pick-existing-0", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.SSH_METHOD)
            screen.query_one("#ssh-method-gist", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            assert screen.current_stage is SetupStage.PUBLISH

    async def test_add_to_github_routes_to_gh_add(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gh=True, gh_authed=True),
            identity=identity("alice"),
            existing_ssh=(make_existing_ssh(tmp_path),),
            plan=SetupPlan(intervention=SetupIntervention.KEY_PICK),
        ))
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.KEY_PICK)
            screen.query_one("#key-pick-existing-0", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.SSH_METHOD)
            screen.query_one("#ssh-method-gh-add", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.GH_ADD)
            assert screen.current_stage is SetupStage.GH_ADD


# ===========================================================================
# Screen: Email → Inbox; Inbox → Done
# ===========================================================================


class TestEmailInboxTransitions:
    async def test_email_send_transitions_to_inbox(
        self, auth_unauthorized, stub_discovery,
    ):
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gpg=True),
            identity=identity(),
            plan=SetupPlan(route_managed_gpg_email()),
        ))
        with patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_gpg_key",
            return_value=GPGKeyInfo(
                fpr="DEADBEEFCAFE0001", email="alice@example.com", algo="ed25519",
            ),
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.upload_openpgp_key",
            return_value=("token", {"alice@example.com": "unpublished"}),
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.request_openpgp_verify",
            return_value=None,
        ), patch(
            "cc_sentiment.signing.GPGBackend.public_key_text",
            return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----",
        ):
            async with SetupHarness(AppState()).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.EMAIL)
                screen.query_one("#email-input", Input).value = "alice@example.com"
                screen.query_one("#email-go", Button).press()
                screen = await wait_for_stage(pilot, SetupStage.INBOX)
                assert screen.current_stage is SetupStage.INBOX

    async def test_inbox_verifies_and_lands_on_done(
        self, auth_ok, stub_discovery,
    ):
        state = AppState(pending_setup=pending_gpg_email_sent())
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.DONE)
            assert screen.current_stage is SetupStage.DONE


# ===========================================================================
# Screen: SavedRetry → Done | Welcome
# ===========================================================================


class TestSavedRetryTransitions:
    async def test_retry_button_succeeds_to_done(
        self, tmp_path: Path, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        results = [AuthUnreachable(detail="dns failure"), AuthOk()]
        with patch(
            "cc_sentiment.upload.Uploader.probe_credentials",
            new_callable=AsyncMock,
            side_effect=lambda _config: results.pop(0),
        ):
            async with SetupHarness(state).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.SAVED_RETRY)
                screen.query_one("#saved-retry-go", Button).press()
                screen = await wait_for_stage(pilot, SetupStage.DONE)
                assert screen.current_stage is SetupStage.DONE

    async def test_set_up_again_routes_to_welcome(
        self, tmp_path: Path, auth_unreachable, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.SAVED_RETRY)
            screen.query_one("#saved-retry-restart", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.WELCOME)
            assert screen.current_stage is SetupStage.WELCOME


# ===========================================================================
# Screen: Trouble → Publish | Email | Welcome
# ===========================================================================


class TestTroubleTransitions:
    async def _enter_trouble(self, pilot, error: str = "timeout") -> None:
        screen = pilot.app.screen
        screen._enter_trouble(error)
        await pilot.pause()

    async def test_edit_username_routes_to_publish(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        with patch(
            "cc_sentiment.tui.setup_helpers.IdentityProbe.validate_username",
            return_value="ok",
        ):
            async with SetupHarness(state).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
                await self._enter_trouble(pilot)
                screen.query_one("#trouble-username-input", Input).value = "bob"
                screen.query_one("#trouble-edit", Button).press()
                await wait_until(
                    pilot, lambda: screen.current_stage is SetupStage.PUBLISH,
                )
                assert screen.current_stage is SetupStage.PUBLISH

    async def test_email_option_routes_to_email(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gpg=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_gpg_email()),
        ))
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            await self._enter_trouble(pilot)
            screen.query_one("#trouble-email", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.EMAIL)
            assert screen.current_stage is SetupStage.EMAIL

    async def test_restart_routes_to_welcome(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True),
            identity=identity(),
            plan=SetupPlan(intervention=SetupIntervention.USERNAME),
        ))
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            await self._enter_trouble(pilot)
            screen.query_one("#trouble-restart", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.WELCOME)
            assert screen.current_stage is SetupStage.WELCOME


# ===========================================================================
# Screen: Publish — fallback panel + no-github link
# ===========================================================================


class TestPublishBehaviors:
    async def test_no_github_link_routes_to_email_with_gpg(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_ssh_keygen=True, has_gpg=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_gpg_email()),
        ))
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            link = screen.query_one("#publish-no-github", LinkRow)
            link.post_message(LinkRow.Pressed(link))
            screen = await wait_for_stage(pilot, SetupStage.EMAIL)
            assert screen.current_stage is SetupStage.EMAIL
            assert screen.github_lookup_allowed is False

    async def test_clipboard_browser_failure_shows_fallback_panel(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        with patch(
            "cc_sentiment.tui.system.Clipboard.copy", return_value=False,
        ), patch("cc_sentiment.tui.system.Browser.open", return_value=False):
            async with SetupHarness(state).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
                fallback = screen.query_one("#publish-fallback-key", Static)
                assert fallback.display
                assert "ssh-ed25519 AAAA cc-sentiment" in str(fallback.render())

    async def test_clipboard_browser_failure_blocks_polling_until_confirmed(
        self, tmp_path: Path, auth_unauthorized, stub_discovery,
    ):
        state = AppState(pending_setup=pending_ssh_gist(tmp_path))
        with patch(
            "cc_sentiment.tui.system.Clipboard.copy", return_value=False,
        ), patch("cc_sentiment.tui.system.Browser.open", return_value=False):
            async with SetupHarness(state).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
                assert screen.gist_watch_worker is None
                screen.query_one("#publish-fallback-confirm", Button).press()
                await pilot.pause()
                assert screen.gist_watch_worker is not None


# ===========================================================================
# Auth gating: candidate is staged but not committed until verified
# ===========================================================================


class TestCandidateGating:
    async def test_candidate_staged_but_state_unsaved_until_verified(
        self, tmp_path: Path, stub_discovery,
    ):
        seeded = make_ssh_info(tmp_path)
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_ssh_gist()),
        ))
        state = AppState()
        with patch(
            "cc_sentiment.upload.Uploader.probe_credentials",
            new_callable=AsyncMock,
            return_value=AuthUnauthorized(status=401),
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.create_gist_from_text",
            return_value="abc123def456",
        ), patch(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            return_value=gist_metadata(
                "alice", "abc123def456", "ssh-ed25519 AAAA cc-sentiment",
            ),
        ):
            async with SetupHarness(state).run_test() as pilot:
                screen = pilot.app.screen
                await wait_until(
                    pilot, lambda: screen.aggregate.candidate.config is not None,
                )
        assert state.config is None


# ===========================================================================
# Full happy path: discovery → working → done with state persisted
# ===========================================================================


class TestHappyPath:
    async def test_managed_gist_flow_completes_to_done(
        self, tmp_path: Path, auth_ok, stub_discovery,
    ):
        seeded = make_ssh_info(tmp_path)
        stub_discovery(DiscoveryResult(
            capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity=identity("alice"),
            plan=SetupPlan(route_managed_ssh_gist()),
        ))
        with patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
            return_value=seeded,
        ), patch(
            "cc_sentiment.signing.discovery.KeyDiscovery.create_gist_from_text",
            return_value="abc123def456",
        ), patch(
            "cc_sentiment.tui.setup_helpers.GistDiscovery.fetch_metadata",
            return_value=gist_metadata(
                "alice", "abc123def456", "ssh-ed25519 AAAAPUBKEY cc-sentiment",
            ),
        ):
            state = AppState()
            async with SetupHarness(state).run_test() as pilot:
                screen = await wait_for_stage(pilot, SetupStage.DONE)
                assert screen.current_stage is SetupStage.DONE
                assert isinstance(state.config, GistConfig)
                assert state.config.gist_id == "abc123def456"
                assert state.config.contributor_id == "alice"


# ===========================================================================
# Dialog dismiss behavior
# ===========================================================================


class TestDialogDismiss:
    async def test_escape_dismisses_with_false(self, stub_discovery):
        harness = SetupHarness(AppState())
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.5)
            await pilot.press("escape")
            await pilot.pause()
        assert harness.dismissed is False

    async def test_done_button_dismisses_with_true(
        self, tmp_path: Path, auth_ok, stub_discovery,
    ):
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("alice"),
                key_path=tmp_path / "id_ed25519",
            ),
        )
        harness = SetupHarness(state)
        async with harness.run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.DONE)
            assert screen.current_stage is SetupStage.DONE
            screen.query_one("#done-btn", Button).press()
            await pilot.pause()
        assert harness.dismissed is True
