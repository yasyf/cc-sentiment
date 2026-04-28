from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.containers import Center
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Button, Static

from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card import Card


class FallbackPanel(Card):
    DEFAULT_CSS: ClassVar[str] = """
    FallbackPanel {
        margin: 1 0;
        border: round $warning;
    }
    FallbackPanel > Static#fallback-key-text {
        width: 100%;
        color: $text;
        margin: 1 0;
    }
    FallbackPanel > Static#fallback-url {
        width: 100%;
        color: $text-muted;
        margin: 0 0 1 0;
    }
    FallbackPanel > Center > Button#fallback-confirm-btn { width: auto; }
    """

    visible: reactive[bool] = reactive(False)

    @dataclass
    class Confirmed(Message):
        pass

    def __init__(
        self,
        *,
        key_text: str,
        target_url: str,
        intro: str = "Copy your signature below, then paste it into a new public gist.",
        confirm_label: str = "I've created the gist",
        title: str = "Manual copy",
    ) -> None:
        super().__init__(title=title, id="fallback-panel")
        self.key_text = key_text
        self.target_url = target_url
        self.intro = intro
        self.confirm_label = confirm_label

    def compose(self) -> ComposeResult:
        yield Body(self.intro)
        yield Static(self.key_text, id="fallback-key-text")
        yield Static(self.target_url, id="fallback-url")
        yield Center(Button(self.confirm_label, id="fallback-confirm-btn", variant="primary"))

    def watch_visible(self, value: bool) -> None:
        self.display = value

    @on(Button.Pressed, "#fallback-confirm-btn")
    def handle_confirm(self) -> None:
        self.post_message(self.Confirmed())
