from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label

from cc_sentiment.tui.screens.dialog import Dialog


class PlatformErrorScreen(Dialog[None]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    PlatformErrorScreen > #dialog-box { border: heavy $error; }
    PlatformErrorScreen > #dialog-box .title { color: $error; }
    """

    BINDINGS = [("q", "done", "Quit"), ("escape", "done", "Quit"), ("enter", "done", "Quit")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label("Sorry, this machine can't run cc-sentiment.", classes="title")
            yield Label(self.message, classes="detail")
            yield Button("Quit", id="quit-btn", variant="primary")

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)
