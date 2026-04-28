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
        text-align: center;
        padding: 0;
        margin: 0;

        &:hover {
            color: $text;
            text-style: underline;
        }
        &:focus {
            color: $accent;
            text-style: underline;
        }
    }
    """

    can_focus = True

    class Pressed(Message):
        def __init__(self, link: LinkRow) -> None:
            super().__init__()
            self.link = link

        @property
        def control(self) -> LinkRow:
            return self.link

    def __init__(self, label: str, *, id: str | None = None, classes: str | None = None) -> None:
        super().__init__(label, id=id, classes=classes)

    def on_click(self, event: events.Click) -> None:
        event.stop()
        self.post_message(self.Pressed(self))

    def on_key(self, event: events.Key) -> None:
        if event.key in ("enter", "space"):
            event.stop()
            self.post_message(self.Pressed(self))
