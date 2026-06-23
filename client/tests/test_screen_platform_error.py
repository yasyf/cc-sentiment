from __future__ import annotations

from unittest.mock import patch

from textual.app import App

from cc_sentiment.engines import (
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeStatus,
)
from cc_sentiment.tui.popovers import PlatformErrorScreen
from cc_sentiment.tui.widgets import CommandBox

CLAUDE_INSTALL_HINT = "curl -fsSL https://claude.ai/install.sh | bash"


def not_installed() -> ClaudeNotInstalled:
    return ClaudeNotInstalled(binary="claude", install_hint=CLAUDE_INSTALL_HINT)


class ErrorHarness(App[None]):
    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__()
        self.status = status
        self.dismissed: object = "not-yet"

    def on_mount(self) -> None:
        self.push_screen(PlatformErrorScreen(self.status), self._capture)

    def _capture(self, result: object) -> None:
        self.dismissed = result


async def test_platform_error_not_installed_shows_brew_install():
    harness = ErrorHarness(not_installed())
    with patch("cc_sentiment.tui.popovers.platform_error.KeyDiscovery.has_brew", return_value=True):
        async with harness.run_test() as pilot:
            await pilot.pause()
            boxes = pilot.app.screen.query(CommandBox)
            commands = [b.command for b in boxes]
            assert "brew install --cask claude-code" in commands
            assert "claude auth login" in commands


async def test_platform_error_not_installed_without_brew_shows_curl():
    harness = ErrorHarness(not_installed())
    with patch("cc_sentiment.tui.popovers.platform_error.KeyDiscovery.has_brew", return_value=False):
        async with harness.run_test() as pilot:
            await pilot.pause()
            commands = [b.command for b in pilot.app.screen.query(CommandBox)]
            assert any("install.sh" in c for c in commands)
            assert "claude auth login" in commands


async def test_platform_error_not_authenticated_shows_auth_only():
    harness = ErrorHarness(ClaudeNotAuthenticated(binary="claude"))
    async with harness.run_test() as pilot:
        await pilot.pause()
        commands = [b.command for b in pilot.app.screen.query(CommandBox)]
        assert commands == ["claude auth login"]


async def test_platform_error_quit_dismisses():
    harness = ErrorHarness(ClaudeNotAuthenticated(binary="claude"))
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit-btn")
        await pilot.pause()
        assert harness.dismissed is None
