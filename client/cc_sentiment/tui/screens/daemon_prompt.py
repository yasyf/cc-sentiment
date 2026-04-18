from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label


class DaemonPromptScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    #daemon-box { width: 76; height: auto; border: heavy $accent; padding: 2 3; }
    #daemon-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #daemon-box .detail { color: $text-muted; margin: 0 0 1 0; }
    #daemon-box Button { margin: 1 1 0 0; }
    """

    BINDINGS = [("escape", "cancel", "Skip")]

    def compose(self) -> ComposeResult:
        with Vertical(id="daemon-box"):
            yield Label("Run this automatically each day?", classes="title")
            yield Label(
                "We can schedule a background job that refreshes your numbers daily. "
                "No need to remember to run this.",
                classes="detail",
            )
            yield Label(
                "Nothing else changes. Undo any time with [b]cc-sentiment uninstall[/].",
                classes="detail",
            )
            with Horizontal():
                yield Button("Schedule it", id="daemon-yes", variant="primary")
                yield Button("Not now", id="daemon-no", variant="default")

    @on(Button.Pressed, "#daemon-yes")
    def on_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#daemon-no")
    def on_no(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
