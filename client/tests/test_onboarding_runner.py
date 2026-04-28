from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App, ComposeResult

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from cc_sentiment.onboarding import Capabilities
from cc_sentiment.onboarding.state import (
    ExistingKey,
    ExistingKeys,
    Identity,
)
from cc_sentiment.tui.onboarding.runner import OnboardingScreen
from cc_sentiment.upload import AuthOk, AuthUnauthorized, AuthUnreachable


SSH_CFG = SSHConfig(
    contributor_id=ContributorId("alice"),
    key_path=Path("/home/.ssh/id_ed25519"),
)


class Harness(App[None]):
    def __init__(self, app_state: AppState) -> None:
        super().__init__()
        self.app_state = app_state
        self.result: bool | None = None

    def compose(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.push_screen(OnboardingScreen(self.app_state), self._on_done)

    def _on_done(self, result: bool | None) -> None:
        self.result = result
        self.exit()


@pytest.fixture
def fast_caps(monkeypatch):
    fake = MagicMock(spec=Capabilities)
    fake.has_ssh_keygen = True
    fake.has_gpg = False
    fake.has_gh = False
    fake.gh_authenticated = False
    fake.has_brew = False
    monkeypatch.setattr(
        "cc_sentiment.onboarding.capabilities.Capabilities.get",
        AsyncMock(return_value=fake),
    )
    return fake


async def test_saved_config_ok_finishes_with_true(fast_caps):
    state = AppState(config=SSH_CFG)
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        harness = Harness(state)
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.done import DoneView
            assert isinstance(pilot.app.screen, DoneView)
            await pilot.click("#start-btn")
            await pilot.pause(delay=0.2)
    assert harness.result is True


async def test_saved_config_invalid_routes_to_welcome_then_discovery_to_blocked(fast_caps):
    fast_caps.has_ssh_keygen = False
    fast_caps.has_gpg = False
    state = AppState(config=SSH_CFG)
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnauthorized(status=401),
    ), patch(
        "cc_sentiment.onboarding.discovery.IdentityProbe.detect",
        new_callable=AsyncMock,
        return_value=Identity(),
    ), patch(
        "cc_sentiment.onboarding.discovery.LocalKeysProbe.detect_all",
        new_callable=AsyncMock,
        return_value=ExistingKeys(),
    ), patch(
        "cc_sentiment.onboarding.discovery.AutoVerify.probe",
        new_callable=AsyncMock,
        return_value=None,
    ):
        harness = Harness(state)
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.welcome import WelcomeView
            assert isinstance(pilot.app.screen, WelcomeView)
            await pilot.click("#get-started-btn")
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.blocked import BlockedView
            assert isinstance(pilot.app.screen, BlockedView)
            await pilot.click("#quit-btn")
            await pilot.pause(delay=0.2)
    assert harness.result is False


async def test_discovery_auto_verifies_existing_key_and_dones(fast_caps):
    state = AppState()
    fast_caps.has_ssh_keygen = True
    existing = ExistingKeys(
        ssh=(
            ExistingKey(
                fingerprint="ssh-ed25519 AAAA",
                label="id_ed25519",
                path=Path("/home/.ssh/id_ed25519"),
                algorithm="ssh-ed25519",
            ),
        ),
    )
    with patch(
        "cc_sentiment.onboarding.discovery.IdentityProbe.detect",
        new_callable=AsyncMock,
        return_value=Identity(github_username="alice"),
    ), patch(
        "cc_sentiment.onboarding.discovery.LocalKeysProbe.detect_all",
        new_callable=AsyncMock,
        return_value=existing,
    ), patch(
        "cc_sentiment.onboarding.discovery.AutoVerify.probe",
        new_callable=AsyncMock,
        return_value=SSH_CFG,
    ):
        harness = Harness(state)
        async with harness.run_test() as pilot:
            await pilot.pause(delay=1.0)
            from cc_sentiment.onboarding.ui.screens.done import DoneView
            assert isinstance(pilot.app.screen, DoneView)
            await pilot.click("#start-btn")
            await pilot.pause(delay=0.5)
    assert harness.result is True
    assert state.config == SSH_CFG


async def test_trouble_state_with_gist_timeout_renders_gist_trouble(fast_caps):
    """Plan: gist never found → GistTrouble (with username edit + email)."""
    from cc_sentiment.onboarding import Stage, State as GlobalState
    from cc_sentiment.onboarding.state import GistTimeout
    from cc_sentiment.tui.onboarding.runner import OnboardingScreen

    state = AppState()
    screen = OnboardingScreen(state)
    gs = GlobalState(
        stage=Stage.TROUBLE,
        trouble=GistTimeout(),
        identity=Identity(github_username="alice"),
    )
    screen.caps = MagicMock(has_gpg=False)
    cls = OnboardingScreen._trouble_screen_for(gs.trouble)
    from cc_sentiment.onboarding.ui.screens.gist_trouble import GistTroubleScreen
    assert cls is GistTroubleScreen


async def test_trouble_state_with_verify_timeout_renders_verify_trouble(fast_caps):
    """Plan: gist found but verify failed → VerifyTrouble (restart only)."""
    from cc_sentiment.onboarding.state import VerifyTimeout
    from cc_sentiment.tui.onboarding.runner import OnboardingScreen

    cls = OnboardingScreen._trouble_screen_for(VerifyTimeout(error_code="key-not-found"))
    from cc_sentiment.onboarding.ui.screens.verify_trouble import VerifyTroubleScreen
    assert cls is VerifyTroubleScreen


async def test_saved_config_unreachable_shows_saved_retry_then_restart(fast_caps):
    fast_caps.has_ssh_keygen = False
    state = AppState(config=SSH_CFG)
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnreachable(detail="dns"),
    ), patch(
        "cc_sentiment.onboarding.discovery.IdentityProbe.detect",
        new_callable=AsyncMock,
        return_value=Identity(),
    ), patch(
        "cc_sentiment.onboarding.discovery.LocalKeysProbe.detect_all",
        new_callable=AsyncMock,
        return_value=ExistingKeys(),
    ), patch(
        "cc_sentiment.onboarding.discovery.AutoVerify.probe",
        new_callable=AsyncMock,
        return_value=None,
    ):
        harness = Harness(state)
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.saved_retry import SavedRetryView
            assert isinstance(pilot.app.screen, SavedRetryView)
            await pilot.click("#restart-link")
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.welcome import WelcomeView
            assert isinstance(pilot.app.screen, WelcomeView)
            await pilot.click("#get-started-btn")
            await pilot.pause(delay=0.5)
            from cc_sentiment.onboarding.ui.screens.blocked import BlockedView
            assert isinstance(pilot.app.screen, BlockedView)
            await pilot.click("#quit-btn")
            await pilot.pause(delay=0.2)
    assert harness.result is False
