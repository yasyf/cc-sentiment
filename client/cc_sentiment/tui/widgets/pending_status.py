from __future__ import annotations

from time import monotonic
from typing import ClassVar

from rich.text import Text
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widgets import LoadingIndicator, Static


class PendingSpinner(LoadingIndicator):
    DEFAULT_CSS = """
    PendingSpinner {
        width: 1;
        min-width: 1;
        height: 1;
        min-height: 1;
        color: $accent;
    }
    """

    FRAMES: ClassVar[tuple[str, ...]] = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.started_at = monotonic()

    def on_mount(self) -> None:
        self.call_after_refresh(self._set_refresh_rate)

    def _set_refresh_rate(self) -> None:
        self.auto_refresh = 1 / 12

    def render(self) -> Text:
        return Text(self.FRAMES[int((monotonic() - self.started_at) * 12) % len(self.FRAMES)])


class PendingStatus(Horizontal):
    DEFAULT_CSS = """
    PendingStatus {
        width: 100%;
        height: auto;
        min-height: 1;
        align-vertical: middle;
        align-horizontal: center;
    }
    PendingStatus > PendingSpinner { margin: 0 1 0 0; }
    PendingStatus > .pending-status-label {
        width: auto;
        color: $text-muted;
    }
    """

    label: reactive[str] = reactive("")

    def __init__(self, label: str, **kwargs) -> None:
        self.label_widget = Static(label, classes="pending-status-label")
        super().__init__(
            PendingSpinner(),
            self.label_widget,
            **kwargs,
        )
        self.label = label

    def watch_label(self, label: str) -> None:
        self.label_widget.update(label)
