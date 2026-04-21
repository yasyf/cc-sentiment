from __future__ import annotations

from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widgets import Static


class KeyPreview(VerticalScroll):
    DEFAULT_CSS = """
    KeyPreview {
        width: 100%;
        height: auto;
        max-height: 6;
        border: round $panel-lighten-2;
        background: $boost;
        padding: 0 1;
    }
    KeyPreview > .key-preview-content {
        width: 100%;
        color: $text-muted;
    }
    """

    text: reactive[str] = reactive("")

    def __init__(self, text: str, **kwargs) -> None:
        self.content_widget = Static(text, classes="key-preview-content")
        super().__init__(
            self.content_widget,
            **kwargs,
        )
        self.text = text

    def watch_text(self, text: str) -> None:
        self.content_widget.update(text)
