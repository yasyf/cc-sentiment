from __future__ import annotations

import sys
from contextlib import suppress
from typing import TYPE_CHECKING

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.widgets import Button, Static

from cc_sentiment.tui.screens.setup.copy import (
    BLOCKED_BODY,
    BLOCKED_INSTALL_HINT_BREW,
    BLOCKED_INSTALL_HINT_GENERIC,
    BLOCKED_TITLE,
)
from cc_sentiment.tui.setup_state import (
    DiscoveryResult,
    SetupStage,
    Tone,
)
from cc_sentiment.tui.system import Browser
from cc_sentiment.tui.widgets import Card

if TYPE_CHECKING:
    from cc_sentiment.tui.screens.setup.screen import SetupScreen  # noqa: F401


SSH_INSTALL_URL = "https://docs.github.com/en/authentication/connecting-to-github-with-ssh/checking-for-existing-ssh-keys"
GPG_INSTALL_URL = "https://gnupg.org/download/index.html"


class BlockedStageMixin:
    def _compose_blocked(self: "SetupScreen") -> ComposeResult:
        with Vertical(id=SetupStage.BLOCKED.value):
            yield Card(
                Static(BLOCKED_BODY, classes="copy"),
                Static("", id="blocked-detail", classes="copy"),
                Static("", id="blocked-status", classes="status-line"),
                Vertical(
                    Button("Open install guide", id="blocked-install", variant="primary"),
                    Button("Quit", id="blocked-quit", variant="default"),
                    classes="blocked-actions",
                ),
                title=BLOCKED_TITLE,
                id="blocked-card",
            )

    def _blocked_on_mount(self: "SetupScreen") -> None:
        self.query_one("#blocked-status", Static).display = False

    def _render_blocked(self: "SetupScreen", result: DiscoveryResult) -> None:
        caps = result.capabilities
        hint = BLOCKED_INSTALL_HINT_BREW if caps.has_brew or sys.platform == "darwin" else BLOCKED_INSTALL_HINT_GENERIC
        with suppress(NoMatches):
            self.query_one("#blocked-detail", Static).update(hint)

    @on(Button.Pressed, "#blocked-install")
    def on_blocked_install(self: "SetupScreen") -> None:
        url = SSH_INSTALL_URL if not self.discovery.capabilities.has_ssh_keygen else GPG_INSTALL_URL
        if not Browser.open(url):
            self._update_status("blocked-status", f"Open manually: {url}", Tone.WARNING)

    @on(Button.Pressed, "#blocked-quit")
    def on_blocked_quit(self: "SetupScreen") -> None:
        self.dismiss(False)
