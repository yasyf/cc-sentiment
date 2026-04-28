from __future__ import annotations

from typing import ClassVar

from textual.widgets import Static


class Body(Static):
    DEFAULT_CSS: ClassVar[str] = """
    Body {
        width: 100%;
        height: auto;
        color: $text;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, text: str, *, id: str | None = "body") -> None:
        super().__init__(text, id=id)
