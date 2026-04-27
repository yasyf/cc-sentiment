from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
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
from cc_sentiment.signing.discovery import GIST_DESCRIPTION
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui.screens import SetupScreen
from cc_sentiment.tui.screens.setup import (
    DISCOVER_BODY,
    DISCOVER_TITLE,
    FIX_BODY,
    FIX_HELP,
    FIX_TITLE,
    MANUAL_GIST_STEPS,
    RESUME_COPY,
    SETTINGS_BODY,
    SETTINGS_TITLE,
    USERNAME_ERROR_NOT_FOUND,
)
from cc_sentiment.tui.setup_helpers import (
    ACCOUNT_SSH_WARNING,
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
from cc_sentiment.tui.widgets.done_branch import (
    PAYLOAD_EXCLUSION_TEXT,
    SETTINGS_PRIMARY_LABEL,
    WHAT_GETS_SENT_TEXT,
)
from cc_sentiment.upload import AuthOk, AuthUnauthorized


def _capabilities(**overrides) -> ToolCapabilities:
    return ToolCapabilities(**overrides)


def _identity(username: str = "", email: str = "", email_usable: bool = False) -> IdentityDiscovery:
    return IdentityDiscovery(github_username=username, github_email=email, email_usable=email_usable)


def _ssh_key(path: str = "/home/.ssh/id_ed25519") -> ExistingSSHKey:
    return ExistingSSHKey(
        info=SSHKeyInfo(path=Path(path), algorithm="ssh-ed25519", comment="alice"),
    )


def _write_ssh_key(tmp_path: Path) -> Path:
    key_path = tmp_path / "id_ed25519"
    key_path.write_text("private")
    key_path.with_suffix(".pub").write_text("ssh-ed25519 AAAA cc-sentiment")
    return key_path


def _managed_existing_ssh_key(tmp_path: Path) -> ExistingSSHKey:
    return ExistingSSHKey(
        info=SSHKeyInfo(
            path=_write_ssh_key(tmp_path),
            algorithm="ssh-ed25519",
            comment="cc-sentiment",
        ),
        managed=True,
    )


def _gpg_key(fpr: str = "ABCDEF1234567890ABCDEF1234567890ABCDEF12") -> ExistingGPGKey:
    return ExistingGPGKey(
        info=GPGKeyInfo(fpr=fpr, email="alice@example.com", algo="ed25519"),
    )


# --------------------------------------------------------------------------- #
# Planner
# --------------------------------------------------------------------------- #


class TestPlanner:
    def test_gh_authenticated_recommends_managed_ssh_gist(self):
        caps = _capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True)
        ident = _identity("alice")
        plan = SetupRoutePlanner.plan(caps, ident, (), ())
        recommended = plan.recommended
        alternatives = plan.alternatives
        assert recommended is not None
        assert recommended.route_id is RouteId.MANAGED_SSH_GIST
        assert recommended.publish_method is PublishMethod.GIST_AUTO
        assert isinstance(recommended.key_plan, GenerateSSHKey)
        assert "Doesn't add a login key" in recommended.safety_note

    def test_no_gh_with_gpg_recommends_managed_gpg_openpgp(self):
        caps = _capabilities(has_gpg=True)
        recommended = SetupRoutePlanner.plan(caps, _identity(), (), ()).recommended
        assert recommended is not None
        assert recommended.route_id is RouteId.MANAGED_GPG_OPENPGP
        assert recommended.publish_method is PublishMethod.OPENPGP
        assert isinstance(recommended.key_plan, GenerateGPGKey)

    def test_no_gh_no_gpg_with_ssh_keygen_recommends_managed_ssh_manual_gist(self):
        caps = _capabilities(has_ssh_keygen=True)
        recommended = SetupRoutePlanner.plan(caps, _identity("alice"), (), ()).recommended
        assert recommended is not None
        assert recommended.route_id is RouteId.MANAGED_SSH_MANUAL_GIST
        assert recommended.publish_method is PublishMethod.GIST_MANUAL

    def test_no_gh_no_gpg_with_ssh_keygen_and_no_username_prompts_for_username(self):
        plan = SetupRoutePlanner.plan(_capabilities(has_ssh_keygen=True), _identity(), (), ())
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.USERNAME

    def test_existing_ssh_with_gh_offers_gist_default(self):
        caps = _capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True)
        ident = _identity("alice")
        ssh = _ssh_key()
        plan = SetupRoutePlanner.plan(caps, ident, (ssh,), ())
        recommended = plan.recommended
        alternatives = plan.alternatives
        gist_methods = {
            r.publish_method
            for r in (recommended, *alternatives)
            if r and r.publish_method is PublishMethod.GIST_AUTO
        }
        assert PublishMethod.GIST_AUTO in gist_methods

    def test_existing_ssh_no_gh_uses_manual_gist(self):
        caps = _capabilities(has_ssh_keygen=True)
        ident = _identity("alice")
        ssh = _ssh_key()
        recommended = SetupRoutePlanner.plan(caps, ident, (ssh,), ()).recommended
        assert recommended is not None
        assert recommended.publish_method is not PublishMethod.GITHUB_SSH

    def test_existing_gpg_offered_as_alternative_to_managed_gpg_openpgp(self):
        caps = _capabilities(has_gpg=True)
        gpg = _gpg_key()
        plan = SetupRoutePlanner.plan(caps, _identity(), (), (gpg,))
        recommended = plan.recommended
        assert recommended is not None
        assert recommended.route_id is RouteId.MANAGED_GPG_OPENPGP
        assert recommended.publish_method is PublishMethod.OPENPGP
        assert any(
            alt.route_id is RouteId.EXISTING_GPG_OPENPGP
            and isinstance(alt.key_plan, ExistingGPGKey)
            for alt in plan.alternatives
        )

    def test_no_tools_offers_install_route(self):
        plan = SetupRoutePlanner.plan(_capabilities(), _identity(), (), ())
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.INSTALL_TOOLS

    def test_gh_unauthenticated_offers_signin(self):
        caps = _capabilities(has_gh=True)
        plan = SetupRoutePlanner.plan(caps, _identity(), (), ())
        assert plan.recommended is None
        assert plan.intervention is SetupIntervention.SIGN_IN_GH

    def test_account_key_routes_carry_warning(self):
        caps = _capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True)
        ssh = _ssh_key()
        alternatives = SetupRoutePlanner.plan(caps, _identity("alice"), (ssh,), ()).alternatives
        github_routes = [
            r for r in alternatives if r.route_id is RouteId.EXISTING_SSH_GITHUB
        ]
        assert github_routes
        assert all(r.account_key_warning == ACCOUNT_SSH_WARNING for r in github_routes)


# --------------------------------------------------------------------------- #
# PendingSetupModel
# --------------------------------------------------------------------------- #


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


# --------------------------------------------------------------------------- #
# SetupScreen pilot scenarios
# --------------------------------------------------------------------------- #


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


@pytest.fixture
def stub_discovery(monkeypatch):
    discoveries: list[DiscoveryResult] = []

    def push(result: DiscoveryResult) -> None:
        discoveries.append(result)

    def fake_run(saved_username: str = "", github_lookup_allowed: bool = True) -> DiscoveryResult:
        if discoveries:
            return discoveries.pop(0)
        return DiscoveryResult()

    monkeypatch.setattr(
        "cc_sentiment.tui.screens.setup.DiscoveryRunner.run", staticmethod(fake_run),
    )
    return push


@pytest.fixture(autouse=True)
def isolated_state_path(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        AppState,
        "state_path",
        classmethod(lambda cls: tmp_path / "state.json"),
    )


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


def _managed_ssh_route() -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.MANAGED_SSH_GIST,
        title="Create a cc-sentiment key and public gist",
        detail="Fastest. cc-sentiment-only key, then a public gist.",
        primary_label="Create key and public gist",
        secondary_label="Use a different key",
        publish_method=PublishMethod.GIST_AUTO,
        key_kind=KeyKind.SSH,
        key_plan=GenerateSSHKey(),
        safety_note="This does not add an SSH login key to your GitHub account.",
        automated=True,
    )


def _existing_ssh_github_route(ssh: ExistingSSHKey) -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.EXISTING_SSH_GITHUB,
        title="Add this SSH key to your GitHub account",
        detail="Adds the public SSH key to your GitHub account.",
        primary_label="Add SSH key to GitHub",
        secondary_label="Use a public gist instead",
        publish_method=PublishMethod.GITHUB_SSH,
        key_kind=KeyKind.SSH,
        key_plan=ssh,
        account_key_warning=ACCOUNT_SSH_WARNING,
        automated=True,
    )


def _existing_gpg_openpgp_route(gpg: ExistingGPGKey, automated: bool = False) -> SetupRoute:
    return SetupRoute(
        route_id=RouteId.EXISTING_GPG_OPENPGP,
        title="Publish this GPG public key with keys.openpgp.org",
        detail="keys.openpgp.org will email you before making the public key searchable.",
        primary_label="Send verification email",
        secondary_label="Choose another key",
        publish_method=PublishMethod.OPENPGP,
        key_kind=KeyKind.GPG,
        key_plan=gpg,
        needs_email=not bool(gpg.info.email),
        automated=automated,
    )


# --------------------------------------------------------------------------- #
# Journeys
# --------------------------------------------------------------------------- #


async def test_returning_verified_user_lands_on_settings(
    tmp_path: Path, auth_ok, stub_discovery,
):
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.SETTINGS)
        assert screen.current_stage is SetupStage.SETTINGS
        verification = str(screen.query_one("#done-verification", Static).render())
        assert "Verification: @alice" in verification


async def test_settings_uses_plan_copy_not_dashboard_identity(
    tmp_path: Path, auth_ok, stub_discovery,
):
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.SETTINGS)
        rendered_text = " ".join(
            str(w.render()) for w in screen.query(Static)
        )
        assert PAYLOAD_EXCLUSION_TEXT in rendered_text
        assert "Verification: @alice" in rendered_text
        # Forbidden phrases
        assert "Uploading as" not in rendered_text
        assert "Dashboard identity" not in rendered_text
        assert "Good if you want the dashboard tied to" not in rendered_text
        assert "Public key location" not in rendered_text
        assert "Private key:" not in rendered_text


async def test_settings_primary_button_says_start_ingesting(
    tmp_path: Path, auth_ok, stub_discovery,
):
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.SETTINGS)
        btn = screen.query_one("#done-btn", Button)
        assert str(btn.label) == SETTINGS_PRIMARY_LABEL


async def test_no_routes_falls_through_to_tools(auth_unauthorized, stub_discovery):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(),
        identity=_identity(),
        existing_ssh=(),
        existing_gpg=(),
        plan=SetupPlan(intervention=SetupIntervention.INSTALL_TOOLS),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.TOOLS)
        assert screen.current_stage is SetupStage.TOOLS


async def test_tools_hides_manual_button_when_no_manual_route(auth_unauthorized, stub_discovery):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.INSTALL_TOOLS),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.TOOLS)
        assert not screen.query_one("#tools-secondary", Button).display


async def test_saved_invalid_copy_survives_discovery(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.INSTALL_TOOLS),
    ))
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.TOOLS)
        status = str(screen.query_one("#discover-status", Static).render())
        assert "Saved verification no longer works." in status


async def test_propose_screen_shows_recommended_route(
    auth_unauthorized, stub_discovery,
):
    route = _managed_ssh_route()
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=_identity("alice"),
        existing_ssh=(),
        existing_gpg=(),
        plan=SetupPlan(route),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
        assert screen.current_stage is SetupStage.PROPOSE
        recommendation = str(screen.query_one("#propose-recommendation", Static).render())
        assert route.title == recommendation


async def test_propose_button_label_matches_route(
    auth_unauthorized, stub_discovery,
):
    route = _managed_ssh_route()
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(route),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
        btn = screen.query_one("#propose-go", Button)
        assert str(btn.label) == route.primary_label


async def test_managed_gist_flow_completes_to_settings(
    tmp_path: Path, auth_ok, stub_discovery,
):
    seeded = SSHKeyInfo(
        path=tmp_path / "id_ed25519",
        algorithm="ssh-ed25519",
        comment="cc-sentiment",
    )
    (tmp_path / "id_ed25519").write_text("private")
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAAPUBKEY cc-sentiment")
    route = _managed_ssh_route()
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(route),
    ))
    state = AppState()
    with patch(
        "cc_sentiment.signing.discovery.KeyDiscovery.generate_managed_ssh_key",
        return_value=seeded,
    ), patch(
        "cc_sentiment.signing.discovery.KeyDiscovery.create_gist_from_text",
        return_value="abc123def456",
    ), patch(
        "cc_sentiment.tui.screens.setup.GistDiscovery.fetch_metadata",
        return_value=GistMetadata(
            GistRef("alice", "abc123def456"),
            GIST_DESCRIPTION,
            "ssh-ed25519 AAAAPUBKEY cc-sentiment",
        ),
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            assert screen.current_stage is SetupStage.PROPOSE
            screen.query_one("#propose-go", Button).press()
            for _ in range(40):
                await pilot.pause(delay=0.1)
                if screen.current_stage is SetupStage.SETTINGS:
                    break
            assert screen.current_stage is SetupStage.SETTINGS
            assert isinstance(state.config, GistConfig)
            assert state.config.gist_id == "abc123def456"
            assert state.config.contributor_id == "alice"


async def test_resume_pending_sends_user_to_guide(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = tmp_path / "id_ed25519"
    (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAA cc-sentiment")
    key_path.write_text("private")
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
        screen = await wait_for_stage(pilot, SetupStage.GUIDE)
        assert screen.current_stage is SetupStage.GUIDE
        instructions = str(screen.query_one("#guide-instructions", Static).render())
        assert RESUME_COPY in instructions


async def test_resume_pending_with_missing_ssh_key_clears_pending_and_runs_discovery(
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
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(_managed_ssh_route()),
    ))
    async with SetupHarness(state).run_test() as pilot:
        await wait_for_stage(pilot, SetupStage.PROPOSE)
    assert state.pending_setup is None


async def test_escape_dismisses_setup(stub_discovery):
    state = AppState()
    harness = SetupHarness(state)
    async with harness.run_test() as pilot:
        await pilot.pause(delay=0.5)
        await pilot.press("escape")
        await pilot.pause()
    assert harness.dismissed is False


async def test_done_button_dismisses_true(
    tmp_path: Path, auth_ok, stub_discovery,
):
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    harness = SetupHarness(state)
    async with harness.run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.SETTINGS)
        assert screen.current_stage is SetupStage.SETTINGS
        screen.query_one("#done-btn", Button).press()
        await pilot.pause()
    assert harness.dismissed is True


async def test_inline_username_prompt_shows_when_no_identity_but_routes_need_one(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.DISCOVER)
        assert screen.current_stage is SetupStage.DISCOVER
        assert screen.query_one("#username-input", Input).display


async def test_username_validation_failure_shows_exact_copy(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    with patch(
        "cc_sentiment.tui.screens.setup.IdentityProbe.validate_username",
        return_value="not-found",
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.DISCOVER)
            assert screen.current_stage is SetupStage.DISCOVER
            screen.query_one("#username-input", Input).value = "ghost"
            screen.query_one("#username-next", Button).press()
            await pilot.pause(delay=0.2)
            status = str(screen.query_one("#username-status", Static).render())
            assert USERNAME_ERROR_NOT_FOUND.format(user="ghost") == status


async def test_propose_alternatives_switch_recommendation(
    auth_unauthorized, stub_discovery,
):
    primary = _managed_ssh_route()
    alt = _existing_ssh_github_route(_ssh_key())
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(primary, (alt,)),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
        assert screen.current_stage is SetupStage.PROPOSE
        screen.query_one("#propose-alt", Button).press()
        await pilot.pause(delay=0.1)


async def test_screen_titles_use_exact_plan_copy(stub_discovery):
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        await pilot.pause(delay=0.2)
        screen = pilot.app.screen
        rendered = " ".join(str(w.render()) for w in screen.query(Static))
        assert DISCOVER_TITLE in rendered
        assert DISCOVER_BODY in rendered


async def test_settings_title_and_body_match_plan(
    tmp_path: Path, auth_ok, stub_discovery,
):
    state = AppState(
        config=SSHConfig(
            contributor_id=ContributorId("alice"),
            key_path=tmp_path / "id_ed25519",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.SETTINGS)
        rendered = " ".join(str(w.render()) for w in screen.query(Static))
        assert SETTINGS_TITLE in rendered
        assert SETTINGS_BODY in rendered


def test_manual_gist_steps_are_six_exact_lines():
    assert len(MANUAL_GIST_STEPS) == 6
    assert "description: cc-sentiment public key" in MANUAL_GIST_STEPS[1]
    assert "cc-sentiment.pub" in MANUAL_GIST_STEPS[2]


def test_fix_screen_constants_match_plan():
    assert FIX_TITLE == "Verification still isn't working"
    assert "Try again" in FIX_BODY
    assert "GitHub issue" in FIX_HELP


def test_what_gets_sent_is_payload_exclusion_text():
    assert WHAT_GETS_SENT_TEXT == PAYLOAD_EXCLUSION_TEXT
    assert "transcript text" in WHAT_GETS_SENT_TEXT


def test_done_branch_settings_primary_label():
    assert SETTINGS_PRIMARY_LABEL == "Start ingesting"


# --------------------------------------------------------------------------- #
# Cross-platform copy and clipboard
# --------------------------------------------------------------------------- #


class TestCopyConstantsCrossPlatform:
    def test_tools_copy_split_into_brew_and_generic(self):
        from cc_sentiment.tui.screens.setup import (
            TOOLS_NO_BREW_BREW,
            TOOLS_NO_BREW_GENERIC,
        )

        assert "brew install gh" in TOOLS_NO_BREW_BREW
        assert "Install GitHub CLI" in TOOLS_NO_BREW_GENERIC


class TestClipboardPlatformRouting:
    def test_command_returns_argv_on_known_platform(self, monkeypatch):
        from cc_sentiment.tui.setup_helpers import Clipboard

        monkeypatch.setattr("cc_sentiment.tui.setup_helpers.sys.platform", "linux")
        monkeypatch.setattr(
            "cc_sentiment.tui.setup_helpers.shutil.which",
            lambda name: f"/usr/bin/{name}" if name == "wl-copy" else None,
        )
        cmd = Clipboard.command()
        assert cmd is not None
        assert cmd == ["wl-copy"]

    def test_command_returns_none_when_nothing_present(self, monkeypatch):
        from cc_sentiment.tui.setup_helpers import Clipboard

        monkeypatch.setattr("cc_sentiment.tui.setup_helpers.sys.platform", "linux")
        monkeypatch.setattr("cc_sentiment.tui.setup_helpers.shutil.which", lambda _name: None)
        assert Clipboard.command() is None


class TestGistDiscoveryHelpers:
    def test_parse_ref_extracts_owner_and_id_from_full_url(self):
        from cc_sentiment.tui.setup_helpers import GistDiscovery

        assert GistDiscovery.parse_ref(
            "https://gist.github.com/octocat/abcdef1234567890abcd",
        ) == GistRef("octocat", "abcdef1234567890abcd")

    def test_parse_ref_uses_fallback_owner_for_bare_id(self):
        from cc_sentiment.tui.setup_helpers import GistDiscovery

        assert GistDiscovery.parse_ref(
            "abcdef1234567890abcd",
            "octocat",
        ) == GistRef("octocat", "abcdef1234567890abcd")

    def test_parse_ref_rejects_garbage(self):
        from cc_sentiment.tui.setup_helpers import GistDiscovery

        assert GistDiscovery.parse_ref("not-a-gist-id") is None


# --------------------------------------------------------------------------- #
# Planner edge cases
# --------------------------------------------------------------------------- #


class TestPlannerEdgeCases:
    def test_existing_gpg_only_recommends_managed_gpg_with_existing_alternative(self):
        caps = _capabilities(has_gpg=True)
        gpg = _gpg_key()
        plan = SetupRoutePlanner.plan(caps, _identity(), (), (gpg,))
        recommended = plan.recommended
        assert recommended is not None
        assert recommended.publish_method is PublishMethod.OPENPGP
        assert isinstance(recommended.key_plan, GenerateGPGKey)
        assert any(
            alt.route_id is RouteId.EXISTING_GPG_OPENPGP
            and isinstance(alt.key_plan, ExistingGPGKey)
            for alt in plan.alternatives
        )

    def test_existing_ssh_with_gh_skips_username_gate_for_recommendation(self):
        caps = _capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True)
        recommended = SetupRoutePlanner.plan(caps, _identity(), (), ()).recommended
        assert recommended is not None
        assert recommended.route_id is RouteId.MANAGED_SSH_GIST

    def test_github_disallowed_drops_all_gist_and_account_routes(self):
        caps = _capabilities(
            has_gh=True, gh_authed=True, has_ssh_keygen=True, has_gpg=True,
        )
        ssh = _ssh_key()
        gpg = _gpg_key()
        plan = SetupRoutePlanner.plan(
            caps, _identity("alice"), (ssh,), (gpg,), github_lookup_allowed=False,
        )
        recommended = plan.recommended
        alternatives = plan.alternatives
        assert recommended is not None
        forbidden = {
            PublishMethod.GIST_AUTO,
            PublishMethod.GIST_MANUAL,
            PublishMethod.GITHUB_SSH,
            PublishMethod.GITHUB_GPG,
        }
        all_methods = {r.publish_method for r in (recommended, *alternatives)}
        assert all_methods.isdisjoint(forbidden)
        assert recommended.publish_method is PublishMethod.OPENPGP

    def test_github_disallowed_with_only_gh_capability_falls_back_to_install_tools(self):
        caps = _capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True)
        plan = SetupRoutePlanner.plan(
            caps, _identity("alice"), (), (), github_lookup_allowed=False,
        )
        assert plan.recommended is None
        assert plan.alternatives == ()
        assert plan.intervention is SetupIntervention.INSTALL_TOOLS


# --------------------------------------------------------------------------- #
# Candidate config staging
# --------------------------------------------------------------------------- #


async def test_username_validation_persists_to_app_state(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    with patch(
        "cc_sentiment.tui.screens.setup.IdentityProbe.validate_username",
        return_value="ok",
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.DISCOVER)
            screen.query_one("#username-input", Input).value = "alice"
            screen.query_one("#username-next", Button).press()
            for _ in range(20):
                await pilot.pause(delay=0.1)
                if state.github_username == "alice":
                    break
    assert state.github_username == "alice"


async def test_manual_gist_guide_shows_copy_and_open_fallbacks(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    ssh = _managed_existing_ssh_key(tmp_path)
    route = SetupRoute(
        route_id=RouteId.MANAGED_SSH_MANUAL_GIST,
        title="Publish a managed SSH key in a gist",
        detail="",
        primary_label="Open GitHub gist guide",
        secondary_label="",
        publish_method=PublishMethod.GIST_MANUAL,
        key_kind=KeyKind.SSH,
        key_plan=ssh,
        automated=False,
    )
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(route),
    ))
    state = AppState()
    with patch("cc_sentiment.tui.screens.setup.Clipboard.copy", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.Browser.open", return_value=False):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            screen.query_one("#propose-go", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.GUIDE)
            instructions = str(screen.query_one("#guide-instructions", Static).render())
            assert "Open manually: https://gist.github.com/" in instructions
            assert "Copy this public key:" in instructions
            assert "ssh-ed25519 AAAA cc-sentiment" in instructions
            assert "Copied your public key" not in instructions


async def test_browser_failure_does_not_overwrite_copied_public_key(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    ssh = _managed_existing_ssh_key(tmp_path)
    route = SetupRoute(
        route_id=RouteId.MANAGED_SSH_MANUAL_GIST,
        title="Publish a managed SSH key in a gist",
        detail="",
        primary_label="Open GitHub gist guide",
        secondary_label="",
        publish_method=PublishMethod.GIST_MANUAL,
        key_kind=KeyKind.SSH,
        key_plan=ssh,
        automated=False,
    )
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(route),
    ))
    state = AppState()
    with patch("cc_sentiment.tui.screens.setup.Clipboard.copy", return_value=True) as copy_mock, \
         patch("cc_sentiment.tui.screens.setup.Browser.open", return_value=False):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            screen.query_one("#propose-go", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.GUIDE)
            instructions = str(screen.query_one("#guide-instructions", Static).render())
            assert "Open manually: https://gist.github.com/" in instructions
            assert "Copy this public key:" not in instructions
    copy_mock.assert_called_once_with("ssh-ed25519 AAAA cc-sentiment")


async def test_openpgp_guide_does_not_open_upload_before_email(
    auth_unauthorized, stub_discovery,
):
    route = _existing_gpg_openpgp_route(_gpg_key())
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gpg=True),
        identity=_identity(email="alice@example.com", email_usable=True),
        plan=SetupPlan(route),
    ))
    state = AppState()
    with patch("cc_sentiment.tui.screens.setup.Browser.open") as open_mock:
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            screen.query_one("#propose-go", Button).press()
            await wait_for_stage(pilot, SetupStage.GUIDE)
    open_mock.assert_not_called()


async def test_noreply_email_is_not_prefilled_for_openpgp(
    auth_unauthorized, stub_discovery,
):
    gpg = ExistingGPGKey(
        info=GPGKeyInfo(
            fpr="ABCDEF1234567890ABCDEF1234567890ABCDEF12",
            email="",
            algo="ed25519",
        ),
    )
    route = _existing_gpg_openpgp_route(gpg)
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gpg=True),
        identity=_identity("alice", "123+alice@users.noreply.github.com", False),
        plan=SetupPlan(route),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
        email = screen.query_one("#propose-email", Input)
        assert email.display
        assert email.value == ""


async def test_openpgp_api_failure_uses_manual_upload_fallback(
    auth_unauthorized, stub_discovery,
):
    route = _existing_gpg_openpgp_route(_gpg_key())
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_gpg=True),
        identity=_identity(email="alice@example.com", email_usable=True),
        plan=SetupPlan(route),
    ))
    state = AppState()
    with patch(
        "cc_sentiment.tui.screens.setup.GPGBackend.public_key_text",
        return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey",
    ), patch(
        "cc_sentiment.tui.screens.setup.KeyDiscovery.upload_openpgp_key",
        side_effect=httpx.HTTPError("nope"),
    ), patch(
        "cc_sentiment.tui.screens.setup.Clipboard.copy",
        return_value=False,
    ), patch(
        "cc_sentiment.tui.screens.setup.Browser.open",
        return_value=False,
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            screen.query_one("#propose-go", Button).press()
            screen = await wait_for_stage(pilot, SetupStage.GUIDE)
            screen.query_one("#guide-check", Button).press()
            for _ in range(20):
                await pilot.pause(delay=0.1)
                if state.pending_setup and state.pending_setup.last_status == "manual-openpgp-upload":
                    break
            instructions = str(screen.query_one("#guide-instructions", Static).render())
            assert "Automatic verification failed" in instructions
            assert "Open manually: https://keys.openpgp.org/upload" in instructions
            assert "Copy this public key:" in instructions
    assert state.pending_setup is not None
    assert state.pending_setup.last_status == "manual-openpgp-upload"


async def test_pasted_gist_url_stages_owner_without_saved_username(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = _write_ssh_key(tmp_path)
    state = AppState(
        pending_setup=PendingSetupModel(
            route_id="managed-ssh-manual-gist",
            publish_method="gist-manual",
            key_kind="ssh",
            key_managed=True,
            key_path=key_path,
        ),
    )
    with patch(
        "cc_sentiment.tui.screens.setup.GistDiscovery.fetch_metadata",
        return_value=GistMetadata(
            GistRef("octocat", "abcdef1234567890abcd"),
            GIST_DESCRIPTION,
            "ssh-ed25519 AAAA cc-sentiment",
        ),
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.GUIDE)
            event = type("Event", (), {"value": "https://gist.github.com/octocat/abcdef1234567890abcd"})()
            await screen.on_guide_gist_url(event)
            assert isinstance(screen.aggregate.candidate.config, GistConfig)
            assert screen.aggregate.candidate.config.contributor_id == "octocat"
    assert state.pending_setup is not None
    assert state.pending_setup.username == "octocat"
    assert state.pending_setup.gist_id == "abcdef1234567890abcd"


async def test_manual_gist_description_mismatch_persists_status(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = _write_ssh_key(tmp_path)
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
        "cc_sentiment.tui.screens.setup.GistDiscovery.fetch_metadata",
        return_value=GistMetadata(
            GistRef("alice", "abcdef1234567890abcd"),
            "wrong",
            "ssh-ed25519 AAAA cc-sentiment",
        ),
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.GUIDE)
            staged = await screen._stage_manual_gist(GistRef("alice", "abcdef1234567890abcd"))
            assert staged is False
            assert screen.aggregate.candidate.config is None
    assert state.pending_setup is not None
    assert state.pending_setup.last_status == "gist-description-mismatch"


async def test_resume_pending_with_last_error_renders_error_in_guide(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    key_path = _write_ssh_key(tmp_path)
    state = AppState(
        pending_setup=PendingSetupModel(
            route_id="managed-ssh-manual-gist",
            publish_method="gist-manual",
            key_kind="ssh",
            key_managed=True,
            key_path=key_path,
            username="alice",
            last_status="verify-unauthorized",
            last_error="sentiments.cc still couldn't verify the public key.",
        ),
    )
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.GUIDE)
        instructions = str(screen.query_one("#guide-instructions", Static).render())
        assert "Last issue: sentiments.cc still couldn't verify" in instructions


async def test_resume_choose_another_method_uses_fresh_discovery(
    tmp_path: Path, auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(_managed_ssh_route()),
    ))
    key_path = _write_ssh_key(tmp_path)
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
        screen = await wait_for_stage(pilot, SetupStage.GUIDE)
        screen.query_one("#guide-redo", Button).press()
        screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
        assert screen.current_stage is SetupStage.PROPOSE


async def test_username_skip_marks_github_disallowed(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True, has_gpg=True),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.DISCOVER)
        screen.query_one("#username-skip", Button).press()
        for _ in range(20):
            await pilot.pause(delay=0.1)
            if not screen.github_lookup_allowed:
                break
    assert screen.github_lookup_allowed is False


async def test_username_skip_without_gpg_stays_in_tools_without_manual_loop(
    auth_unauthorized, stub_discovery,
):
    stub_discovery(DiscoveryResult(
        capabilities=_capabilities(has_ssh_keygen=True),
        identity=_identity(),
        plan=SetupPlan(intervention=SetupIntervention.USERNAME),
    ))
    state = AppState()
    async with SetupHarness(state).run_test() as pilot:
        screen = await wait_for_stage(pilot, SetupStage.DISCOVER)
        screen.query_one("#username-skip", Button).press()
        screen = await wait_for_stage(pilot, SetupStage.TOOLS)
        assert screen.current_stage is SetupStage.TOOLS
        assert not screen.query_one("#tools-secondary", Button).display


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
        capabilities=_capabilities(has_gh=True, gh_authed=True, has_ssh_keygen=True),
        identity=_identity("alice"),
        plan=SetupPlan(_managed_ssh_route()),
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
        "cc_sentiment.tui.screens.setup.GistDiscovery.fetch_metadata",
        return_value=GistMetadata(
            GistRef("alice", "abc123def456"),
            GIST_DESCRIPTION,
            "ssh-ed25519 AAAA cc-sentiment",
        ),
    ):
        async with SetupHarness(state).run_test() as pilot:
            screen = await wait_for_stage(pilot, SetupStage.PROPOSE)
            screen.query_one("#propose-go", Button).press()
            for _ in range(20):
                await pilot.pause(delay=0.1)
                if screen.aggregate.candidate.config is not None:
                    break
    assert state.config is None
