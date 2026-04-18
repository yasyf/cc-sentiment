from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.widgets import Static

from cc_sentiment.models import CLIENT_VERSION

from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.view import ProcessingView
from cc_sentiment.tui.widgets import SpinnerLine


class BootingScreen(Dialog[None]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    BootingScreen > #dialog-box { width: 60; padding: 1 2; }
    BootingScreen > #dialog-box #boot-title { text-align: center; text-style: bold; color: $text; }
    BootingScreen > #dialog-box #boot-version { text-align: center; color: $text-muted; margin: 0 0 1 0; }
    BootingScreen > #dialog-box #boot-spinner-row { height: 1; align-horizontal: center; margin: 1 0 0 0; }
    BootingScreen > #dialog-box #boot-spinner { width: 3; }
    BootingScreen > #dialog-box #boot-status { text-align: center; color: $text-muted; height: 1; }
    BootingScreen > #dialog-box #boot-detail { text-align: center; color: $text-muted; height: auto; max-height: 8; margin: 1 0 0 0; }
    """

    status: reactive[str] = reactive("Starting up...")

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Static("cc-sentiment", id="boot-title")
            yield Static(f"v{CLIENT_VERSION}", id="boot-version")
            with Horizontal(id="boot-spinner-row"):
                yield SpinnerLine(id="boot-spinner")
            yield Static("Starting up...", id="boot-status")
            yield Static("", id="boot-detail")

    def watch_status(self, value: str) -> None:
        self.query_one("#boot-status", Static).update(value)

    def append_detail(self, line: str) -> None:
        ProcessingView.append_line(self.query_one("#boot-detail", Static), line)
