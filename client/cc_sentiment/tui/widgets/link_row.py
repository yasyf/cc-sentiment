from __future__ import annotations

from typing import ClassVar

from textual import events
from textual.message import Message
from textual.widgets import Static


class LinkRow(Static):
    DEFAULT_CSS: ClassVar[str] = """
    LinkRow {
        width: 100%;
        height: 1;
        color: $text-muted;
        margin: 0;
        padding: 0;
    }
    LinkRow:hover {
        color: $text;
        text-style: underline;
    }
    """

    class Pressed(Message):
        def __init__(self, link: LinkRow) -> None:
            super().__init__()
            self.link = link

        @property
        def control(self) -> LinkRow:
            return self.link

    def __init__(self, label: str, *, id: str | None = None) -> None:
        super().__init__(label, id=id)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Pressed(self))
