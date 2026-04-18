from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Button, Label


class PlatformErrorScreen(Screen[None]):
    DEFAULT_CSS = """
    PlatformErrorScreen { align: center middle; }
    #error-box { width: 76; height: auto; border: heavy $error; padding: 2 3; }
    #error-box .title { text-style: bold; color: $error; margin: 0 0 1 0; }
    #error-box .detail { color: $text; margin: 0 0 2 0; }
    """

    BINDINGS = [("q", "done", "Quit"), ("escape", "done", "Quit"), ("enter", "done", "Quit")]

    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message

    def compose(self) -> ComposeResult:
        with Vertical(id="error-box"):
            yield Label("Sorry, this machine can't run cc-sentiment.", classes="title")
            yield Label(self.message, classes="detail")
            yield Button("Quit", id="quit-btn", variant="primary")

    @on(Button.Pressed, "#quit-btn")
    def on_quit(self) -> None:
        self.dismiss(None)

    def action_done(self) -> None:
        self.dismiss(None)
