from __future__ import annotations

from typing import ClassVar

from textual.widgets import Static


class ScoreBar(Static):
    DEFAULT_CSS = """
    ScoreBar { height: 1; }
    """

    COLORS: ClassVar[dict[int, str]] = {1: "red", 2: "red", 3: "yellow", 4: "green", 5: "green"}
    LABELS: ClassVar[dict[int, str]] = {1: "frustrated", 2: "annoyed", 3: "neutral", 4: "satisfied", 5: "delighted"}
    ICONS: ClassVar[dict[int, str]] = {1: "😤", 2: "😒", 3: "😐", 4: "😊", 5: "🤩"}

    def __init__(self, score: int) -> None:
        super().__init__()
        self.score = score

    def render_bar(self, count: int, total: int, max_count: int) -> str:
        pct = 100 * count / total if total else 0
        bar_width = 20
        bar_len = int(bar_width * count / max_count) if max_count else 0
        color = self.COLORS[self.score]
        icon = self.ICONS[self.score]
        label = self.LABELS[self.score]
        bar = "━" * bar_len + "╺" + "─" * (bar_width - bar_len)
        return f" {icon} {self.score} [{color}]{label:>11}[/]  [{color}]{bar}[/]  {pct:4.1f}%  ({count})"
