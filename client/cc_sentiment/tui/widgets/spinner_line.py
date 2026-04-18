from __future__ import annotations

from rich.spinner import Spinner
from textual.widgets import Static


class SpinnerLine(Static):
    DEFAULT_CSS = "SpinnerLine { height: 1; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spinner = Spinner("dots", style="bold")

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self.refresh)

    def render(self) -> Spinner:
        return self.spinner
