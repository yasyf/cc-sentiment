from __future__ import annotations

import sys

from textual import on
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.widgets import Button, Label

from cc_sentiment.engines import (
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeStatus,
)

from cc_sentiment.tui.popovers.dialog import Dialog
from cc_sentiment.tui.widgets import CommandBox

INSTALL_BREW = "brew install --cask claude-code"
INSTALL_CURL = "curl -fsSL https://claude.ai/install.sh | bash"
INSTALL_GENERIC = "Install Claude Code from https://claude.ai/code"
AUTH_LOGIN = "claude auth login"


def install_command(brew_available: bool) -> str:
    if brew_available:
        return INSTALL_BREW
    if sys.platform in ("darwin", "linux"):
        return INSTALL_CURL
    return INSTALL_GENERIC


class PlatformErrorScreen(Dialog[None]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    PlatformErrorScreen > #dialog-box { border: heavy $error; }
    PlatformErrorScreen > #dialog-box .title { color: $error; }
    PlatformErrorScreen > #dialog-box CommandBox { margin: 0 0 1 0; }
    PlatformErrorScreen > #dialog-box > Center { width: 100%; margin: 1 0 0 0; }
    """

    BINDINGS = [("q", "done", "Quit"), ("escape", "done", "Quit")]

    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__()
        self.status = status

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            match self.status:
                case ClaudeNotInstalled(brew_available=brew):
                    yield Label("Install Claude Code to continue", classes="title")
                    yield Label(
                        "Install and sign in to Claude Code, then come back. "
                        "Click a command to copy it.",
                        classes="detail",
                    )
                    yield CommandBox(install_command(brew))
                    yield CommandBox(AUTH_LOGIN)
                case ClaudeNotAuthenticated():
                    yield Label("Sign in to Claude Code", classes="title")
                    yield Label(
                        "Run this command, then come back.",
                        classes="detail",
                    )
                    yield CommandBox(AUTH_LOGIN)
            yield Center(Button("Quit", id="quit-btn", variant="primary"))

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)
