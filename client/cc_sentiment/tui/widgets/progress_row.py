from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Label, ProgressBar, Static


class ProgressRow(Horizontal):
    DEFAULT_CSS: ClassVar[str] = """
    ProgressRow { height: 1; }
    ProgressRow.inactive { display: none; }
    ProgressRow > .row-label { width: 12; color: $text-muted; }
    ProgressRow > ProgressBar { width: 1fr; }
    ProgressRow > .row-context { width: auto; min-width: 24; margin: 0 0 0 2; color: $text-muted; }
    """

    def __init__(self, *, label: str, bar_id: str, context_id: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.label_text = label
        self.bar_widget_id = bar_id
        self.context_widget_id = context_id

    def compose(self) -> ComposeResult:
        yield Static(self.label_text, classes="row-label")
        yield ProgressBar(id=self.bar_widget_id, total=100, show_eta=False, show_percentage=True)
        yield Label("", id=self.context_widget_id, classes="row-context")
