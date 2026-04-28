from __future__ import annotations

from typing import ClassVar

from textual.widgets import Static

from cc_sentiment.tui.widgets.card import Card


class KeyPreview(Card):
    DEFAULT_CSS: ClassVar[str] = """
    KeyPreview { margin: 1 0 1 0; }
    KeyPreview > Static#key-preview-text {
        width: 100%;
        color: $text;
    }
    """

    def __init__(self, key_text: str, *, title: str = "Your signature", id: str = "key-preview") -> None:
        super().__init__(
            Static(key_text, id="key-preview-text"),
            title=title,
            id=id,
        )
        self.key_text = key_text
