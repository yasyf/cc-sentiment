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
from cc_sentiment.signing import SSHKeyInfo
from cc_sentiment.tui.screens import SetupScreen
from cc_sentiment.tui.screens.setup.copy import (
    BLOCKED_INSTALL_HINT_BREW,
    BLOCKED_INSTALL_HINT_GENERIC,
    TROUBLE_BODY,
    TROUBLE_KEEP_WATCHING,
    TROUBLE_TITLE,
    TROUBLE_TRY_DIFFERENT,
    USERNAME_ERROR_NOT_FOUND,
    WELCOME_BODY,
    WELCOME_TITLE,
)
from cc_sentiment.tui.setup_helpers import (
    GistDiscovery,
    GistMetadata,
    GistRef,
    SetupRoutePlanner,
)
from cc_sentiment.tui.system import Clipboard
from cc_sentiment.tui.setup_state import (
    DiscoveryResult,
    GenerateGPGKey,
    GenerateSSHKey,
    IdentityDiscovery,
    PublishMethod,
    RouteId,
    SetupIntervention,
    SetupPlan,
    SetupStage,
    ToolCapabilities,
)
from cc_sentiment.tui.widgets.done_branch import (
    PAYLOAD_EXCLUSION_TEXT,
    SETTINGS_PRIMARY_LABEL,
    WHAT_GETS_SENT_TEXT,
)
from cc_sentiment.tui.widgets.link_row import LinkRow
from cc_sentiment.upload import AuthOk, AuthUnauthorized


def capabilities(**overrides) -> ToolCapabilities:
    return ToolCapabilities(**overrides)


def identity(username: str = "", email: str = "", email_usable: bool = False) -> IdentityDiscovery:
    return IdentityDiscovery(
        github_username=username, github_email=email, email_usable=email_usable,
    )


def write_ssh_key(tmp_path: Path) -> Path:
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("private")
    key_path.with_suffix(".pub").write_text("ssh-ed25519 AAAA cc-sentiment")
    return key_path


def gist_metadata(owner: str, gist_id: str, public_key: str) -> GistMetadata:
    return GistMetadata(
        ref=GistRef(owner=owner, gist_id=gist_id),
        description="cc-sentiment public key",
        file_contents={"cc-sentiment.pub": public_key},
    )


class TestPlanner:
    def test_gh_authenticated_recommends_managed_ssh_gist(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity("alice"),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_SSH_GIST
        assert plan.recommended.publish_method is PublishMethod.GIST_AUTO
        assert isinstance(plan.recommended.key_plan, GenerateSSHKey)

    def test_username_present_without_gh_recommends_manual_gist(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_ssh_keygen=True), identity("alice"),
        )
        assert plan.recommended is not None
        assert plan.recommended.route_id is RouteId.MANAGED_SSH_MANUAL_GIST
        assert plan.recommended.publish_method is PublishMethod.GIST_MANUAL

    def test_no_username_with_ssh_keygen_prompts_for_username(self):
        plan = SetupRoutePlanner.plan(capabilities(has_ssh_keygen=True), identity())
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.USERNAME

    def test_missing_ssh_keygen_falls_through_to_blocked(self):
        plan = SetupRoutePlanner.plan(capabilities(), identity("alice"))
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.BLOCKED

    def test_github_disallowed_falls_through_to_blocked(self):
        plan = SetupRoutePlanner.plan(
            capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
            identity("alice"),
            github_lookup_allowed=False,
        )
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.BLOCKED

    def test_alternate_openpgp_route_uses_managed_gpg(self):
        route = SetupRoutePlanner.alternate_openpgp_route()
        assert route.route_id is RouteId.MANAGED_GPG_OPENPGP
        assert route.publish_method is PublishMethod.OPENPGP
        assert isinstance(route.key_plan, GenerateGPGKey)


class TestPendingSetupModel:
    def test_extra_fields_rejected(self):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-ssh-gist",
                publish_method="gist-auto",
                key_kind="ssh",
                key_managed=True,
                key_path=Path("/tmp/k"),
                username="alice",
                location="GitHub gist",
            )

    def test_ssh_requires_key_path(self):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-ssh-gist",
                publish_method="gist-auto",
                key_kind="ssh",
                key_managed=True,
            )

    def test_gpg_requires_key_fpr(self):
        with pytest.raises(Exception):
            PendingSetupModel(
                route_id="managed-gpg-openpgp",
                publish_method="openpgp",
                key_kind="gpg",
                key_managed=True,
            )

    def test_ssh_route_round_trips(self, tmp_path: Path):
        model = PendingSetupModel(
            route_id="managed-ssh-gist",
            publish_method="gist-auto",
            key_kind="ssh",
            key_managed=True,
            key_path=tmp_path / "key",
            username="alice",
        )
        state = AppState(pending_setup=model)
        state.save()
        loaded = AppState.load()
        assert loaded.pending_setup is not None
        assert loaded.pending_setup.key_kind == "ssh"
        assert loaded.pending_setup.key_path == tmp_path / "key"


class TestCopyConstants:
    def test_trouble_constants_match_plan(self):
        assert TROUBLE_TITLE == "Still watching for your gist"
        assert "minute" in TROUBLE_BODY
        assert TROUBLE_KEEP_WATCHING == "Keep watching"
        assert TROUBLE_TRY_DIFFERENT == "Try a different way"

    def test_what_gets_sent_is_payload_exclusion_text(self):
        assert WHAT_GETS_SENT_TEXT == PAYLOAD_EXCLUSION_TEXT
        assert "transcript text" in WHAT_GETS_SENT_TEXT

    def test_done_branch_settings_primary_label(self):
        assert SETTINGS_PRIMARY_LABEL == "Start ingesting"

    def test_blocked_copy_split_into_brew_and_generic(self):
        assert "brew install" in BLOCKED_INSTALL_HINT_BREW
        assert "OpenSSH" in BLOCKED_INSTALL_HINT_GENERIC or "GPG" in BLOCKED_INSTALL_HINT_GENERIC


class TestClipboardPlatformRouting:
    def test_command_returns_argv_on_known_platform(self, monkeypatch):
        monkeypatch.setattr("cc_sentiment.tui.system.sys.platform", "linux")
        monkeypatch.setattr(
            "cc_sentiment.tui.system.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None,
        )
        assert Clipboard.command() == ["wl-copy"]

    def test_command_returns_none_when_nothing_present(self, monkeypatch):
        monkeypatch.setattr("cc_sentiment.tui.system.sys.platform", "linux")
        monkeypatch.setattr("cc_sentiment.tui.system.shutil.which", lambda _name: None)
        assert Clipboard.command() is None


class TestGistContentMatching:
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
        if screen.current_stage is stage:
            return screen
    return screen


async def wait_until(pilot, predicate, *, attempts: int = 40, delay: float = 0.1) -> bool:
    for _ in range(attempts):
        await pilot.pause(delay=delay)
        if predicate():
            return True
    return False


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


async def test_returning_verified_user_lands_on_done(
    tmp_path: Path, auth_ok, stub_discovery,
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
        verification = str(screen.query_one("#done-verification", Static).render())
        assert "Verification: @alice" in verification


async def test_blocked_intervention_lands_on_blocked(auth_unauthorized, stub_discovery):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.BLOCKED),
    ))
    async with SetupHarness(AppState()).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.BLOCKED)
        assert screen.current_stage is SetupStage.BLOCKED
        assert screen.query_one("#blocked-install", Button).display
        assert screen.query_one("#blocked-quit", Button).display


async def test_managed_gist_flow_completes_to_done(
    tmp_path: Path, auth_ok, stub_discovery,
):
    seeded = SSHKeyInfo(
        path=tmp_path / "id_ed25519",
        algorithm="ssh-ed25519",
        comment="cc-sentiment",
    )
    (tmp_path / "id_ed25519").write_text("private")
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAAPUBKEY cc-sentiment")
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=identity("alice"),
        plan=SetupPlan(SetupRoutePlanner._managed_ssh_gist()),
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


async def test_resume_pending_sends_user_to_publish(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = write_ssh_key(tmp_path)
    state = AppState(
        pending_setup=PendingSetupModel(
            route_id="managed-ssh-manual-gist",
            publish_method="gist-manual",
            key_kind="ssh",
            key_managed=True,
            key_path=key_path,
            username="alice",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
        assert screen.current_stage is SetupStage.PUBLISH
        preview = str(screen.query_one("#publish-key-preview", Static).render())
        assert "ssh-ed25519 AAAA cc-sentiment" in preview


async def test_resume_pending_with_missing_ssh_key_clears_pending(
    tmp_path: Path, auth_unauthorized, stub_discovery,
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
        plan=SetupPlan(SetupRoutePlanner._managed_ssh_manual_gist()),
    ))
    async with SetupHarness(state).run_test() as pilot:
        await wait_until(pilot, lambda: state.pending_setup is None, attempts=30)
    assert state.pending_setup is None


async def test_escape_dismisses_setup(stub_discovery):
    harness = SetupHarness(AppState())
    async with harness.run_test() as pilot:
        await pilot.pause(delay=0.5)
        await pilot.press("escape")
        await pilot.pause()
    assert harness.dismissed is False


async def test_done_button_dismisses_true(tmp_path: Path, auth_ok, stub_discovery):
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


async def test_inline_username_prompt_appears_on_username_intervention(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_ssh_keygen=True),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    async with SetupHarness(AppState()).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.WELCOME)
        await wait_until(
            pilot, lambda: screen.query_one("#welcome-username-input", Input).display,
        )
        assert screen.query_one("#welcome-username-input", Input).display
        assert screen.query_one("#welcome-no-github", LinkRow).display


async def test_username_validation_failure_shows_exact_copy(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_ssh_keygen=True),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    with patch(
        "cc_sentiment.tui.setup_helpers.IdentityProbe.validate_username",
        return_value="not-found",
    ):
        async with SetupHarness(AppState()).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.WELCOME)
            await wait_until(
                pilot, lambda: screen.query_one("#welcome-username-input", Input).display,
            )
            screen.query_one("#welcome-username-input", Input).value = "ghost"
            screen.query_one("#welcome-go", Button).press()
            await wait_until(
                pilot,
                lambda: bool(str(screen.query_one("#welcome-username-status", Static).render())),
            )
            status = str(screen.query_one("#welcome-username-status", Static).render())
            assert USERNAME_ERROR_NOT_FOUND.format(user="ghost") == status


async def test_username_validation_persists_to_app_state(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_ssh_keygen=True),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    with patch(
        "cc_sentiment.tui.setup_helpers.IdentityProbe.validate_username",
        return_value="ok",
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.WELCOME)
            await wait_until(
                pilot, lambda: screen.query_one("#welcome-username-input", Input).display,
            )
            screen.query_one("#welcome-username-input", Input).value = "alice"
            screen.query_one("#welcome-go", Button).press()
            await wait_until(pilot, lambda: state.github_username == "alice")
    assert state.github_username == "alice"


async def test_no_github_link_with_gpg_routes_to_alternate(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_ssh_keygen=True, has_gpg=True),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    async with SetupHarness(AppState()).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.WELCOME)
        await wait_until(
            pilot, lambda: screen.query_one("#welcome-no-github", LinkRow).display,
        )
        link = screen.query_one("#welcome-no-github", LinkRow)
        link.post_message(LinkRow.Pressed(link))
        await wait_for_stage(pilot, SetupStage.ALTERNATE)
        assert screen.github_lookup_allowed is False


async def test_no_github_link_without_gpg_lands_on_blocked(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_ssh_keygen=True),
        identity=identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    async with SetupHarness(AppState()).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.WELCOME)
        await wait_until(
            pilot, lambda: screen.query_one("#welcome-no-github", LinkRow).display,
        )
        link = screen.query_one("#welcome-no-github", LinkRow)
        link.post_message(LinkRow.Pressed(link))
        screen = await wait_for_stage(pilot, SetupStage.BLOCKED)
        assert screen.current_stage is SetupStage.BLOCKED


async def test_welcome_renders_plan_copy(stub_discovery):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.2)
        screen = pilot.app.screen
        rendered = " ".join(str(w.render()) for w in screen.query(Static))
        assert WELCOME_TITLE in rendered or WELCOME_TITLE in str(
            screen.query_one("#welcome-card").border_title or "",
        )
        assert WELCOME_BODY in rendered


async def test_publish_no_github_returns_to_alternate(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = write_ssh_key(tmp_path)
    state = AppState(
        pending_setup=PendingSetupModel(
            route_id="managed-ssh-manual-gist",
            publish_method="gist-manual",
            key_kind="ssh",
            key_managed=True,
            key_path=key_path,
            username="alice",
        ),
    )
    with patch("cc_sentiment.tui.system.Browser.open", return_value=False), patch(
        "cc_sentiment.tui.system.Clipboard.copy", return_value=False,
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            link = screen.query_one("#publish-no-github", LinkRow)
            link.post_message(LinkRow.Pressed(link))
            await pilot.pause(delay=0.2)
        assert screen.github_lookup_allowed is False


async def test_publish_no_clipboard_shows_inline_key(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = write_ssh_key(tmp_path)
    state = AppState(
        pending_setup=PendingSetupModel(
            route_id="managed-ssh-manual-gist",
            publish_method="gist-manual",
            key_kind="ssh",
            key_managed=True,
            key_path=key_path,
            username="alice",
        ),
    )
    with patch(
        "cc_sentiment.tui.system.Clipboard.copy", return_value=False,
    ), patch("cc_sentiment.tui.system.Browser.open", return_value=False):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PUBLISH)
            fallback = screen.query_one("#publish-fallback-key", Static)
            assert fallback.display
            assert "ssh-ed25519 AAAA cc-sentiment" in str(fallback.render())


async def test_candidate_config_not_committed_until_auth_ok(
    tmp_path: Path, stub_discovery,
):
    seeded = SSHKeyInfo(
        path=tmp_path / "id_ed25519",
        algorithm="ssh-ed25519",
        comment="cc-sentiment",
    )
    (tmp_path / "id_ed25519").write_text("private")
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAA cc-sentiment")
    stub_discovery(DiscoveryResult(
        capabilities=capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=identity("alice"),
        plan=SetupPlan(SetupRoutePlanner._managed_ssh_gist()),
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
