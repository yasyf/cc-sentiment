from __future__ import annotations

from typing import ClassVar

from textual.widgets import Static


class MutedLine(Static):
    DEFAULT_CSS: ClassVar[str] = """
    MutedLine {
        width: 100%;
        height: auto;
        color: $text-muted;
        text-align: center;
    }
    """

    def __init__(self, text: str, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(text, id=id, classes=classes)
