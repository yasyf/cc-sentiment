from __future__ import annotations

from typing import ClassVar

from textual.widgets import Static


class Title(Static):
    DEFAULT_CSS: ClassVar[str] = """
    Title {
        width: 100%;
        height: auto;
        text-align: center;
        text-style: bold;
        color: $text;
        margin: 0 0 1 0;
    }
    """

    def __init__(self, text: str, *, id: str | None = "title") -> None:
        super().__init__(text, id=id)
