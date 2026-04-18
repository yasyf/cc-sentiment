from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Static


class StatCard(Vertical):
    DEFAULT_CSS = """
    StatCard {
        width: 1fr;
        height: 4;
        padding: 0 1;
        border: tall $primary-background;
    }
    StatCard > .stat-value { text-style: bold; }
    StatCard > .stat-label { color: $text-muted; text-style: bold; }
    """

    def __init__(self, *, value_id: str, label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.value_id = value_id
        self.label_text = label

    def compose(self) -> ComposeResult:
        yield Static("--", id=self.value_id, classes="stat-value")
        yield Static(self.label_text, classes="stat-label")
