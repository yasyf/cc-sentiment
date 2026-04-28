from __future__ import annotations

from enum import Enum
from typing import ClassVar

from textual.reactive import reactive
from textual.widgets import Static


class StatusTone(str, Enum):
    MUTED = "muted"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


class StatusLine(Static):
    DEFAULT_CSS: ClassVar[str] = """
    StatusLine {
        width: 100%;
        min-height: 1;
        height: auto;
        text-align: left;
    }
    StatusLine.muted   { color: $text-muted; }
    StatusLine.success { color: $success; }
    StatusLine.warning { color: $warning; }
    StatusLine.error   { color: $error; }
    """

    text: reactive[str] = reactive("")
    tone: reactive[StatusTone] = reactive(StatusTone.MUTED)

    def __init__(self, *, id: str | None = "status") -> None:
        super().__init__("", id=id, classes=StatusTone.MUTED.value)

    def set(self, text: str, tone: StatusTone = StatusTone.MUTED) -> None:
        self.tone = tone
        self.text = text

    def watch_text(self, text: str) -> None:
        self.update(text)

    def watch_tone(self, old: StatusTone, new: StatusTone) -> None:
        self.remove_class(old.value)
        self.add_class(new.value)
