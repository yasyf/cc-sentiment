from __future__ import annotations

import sys

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label

from cc_sentiment.engines import (
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeStatus,
)

from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.widgets import ButtonRow, CommandBox

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
    PlatformErrorScreen > #dialog-box ButtonRow { align-horizontal: right; }
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
                        "cc-sentiment scores your Claude Code sessions, so you'll need "
                        "Claude Code installed and signed in. Click a command to copy it.",
                        classes="detail",
                    )
                    yield CommandBox(install_command(brew))
                    yield CommandBox(AUTH_LOGIN)
                case ClaudeNotAuthenticated():
                    yield Label("Sign in to Claude Code", classes="title")
                    yield Label(
                        "Claude Code is installed but not signed in. "
                        "Click the command to copy it, then run it in your terminal.",
                        classes="detail",
                    )
                    yield CommandBox(AUTH_LOGIN)
            yield ButtonRow(Button("Quit", id="quit-btn", variant="primary"))

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)
