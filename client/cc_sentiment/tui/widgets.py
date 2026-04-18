from __future__ import annotations

from typing import ClassVar

from rich.spinner import Spinner
from textual.widgets import Static

from cc_sentiment.models import SentimentRecord


class SpinnerLine(Static):
    DEFAULT_CSS = "SpinnerLine { height: 1; }"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.spinner = Spinner("dots", style="bold")

    def on_mount(self) -> None:
        self.set_interval(1 / 12, self.refresh)

    def render(self) -> Spinner:
        return self.spinner


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


class HourlyChart(Static):
    DEFAULT_CSS = """
    HourlyChart { height: 7; }
    """

    COLORS: ClassVar[dict[int, str]] = {1: "red", 2: "dark_orange", 3: "yellow", 4: "green", 5: "cyan"}
    Y_TICKS: ClassVar[dict[int, str]] = {5: "😄", 4: "🙂", 3: "😐", 2: "😕", 1: "😡"}
    X_LABELS: ClassVar[dict[int, str]] = {0: "12a", 6: "6a", 12: "12p", 18: "6p", 23: "11p"}

    def update_chart(self, records: list[SentimentRecord]) -> None:
        counts = [0] * 24
        frustrated = [0] * 24
        for r in records:
            h = r.time.astimezone().hour
            counts[h] += 1
            if int(r.sentiment_score) <= 2:
                frustrated[h] += 1

        max_f = max(frustrated)
        rows: list[int | None] = [
            None if counts[h] == 0
            else 5 if frustrated[h] == 0
            else max(1, 5 - round(4 * frustrated[h] / max_f))
            for h in range(24)
        ]

        if all(r is None for r in rows):
            self.update("[dim]no data yet[/]")
            return

        lines: list[str] = []
        for row_score in range(5, 0, -1):
            tick = self.Y_TICKS[row_score]
            cells: list[str] = []
            for h in range(24):
                if rows[h] == row_score:
                    cells.append(f"[{self.COLORS[row_score]}]●[/]")
                elif self._on_line_segment(h, row_score, rows):
                    cells.append("[dim]│[/]")
                else:
                    cells.append(" ")
            lines.append(f"{tick} " + "".join(cells))

        lines.append("   " + "─" * 24)
        axis_buf = list(" " * 24)
        for h, lbl in self.X_LABELS.items():
            for i, ch in enumerate(lbl):
                if h + i < 24:
                    axis_buf[h + i] = ch
        lines.append("   " + "".join(axis_buf).rstrip())
        self.update("\n".join(lines))

    @staticmethod
    def _on_line_segment(h: int, row_score: int, rows: list[int | None]) -> bool:
        if rows[h] is not None:
            return False
        prev_h = next((i for i in range(h - 1, -1, -1) if rows[i] is not None), None)
        next_h = next((i for i in range(h + 1, 24) if rows[i] is not None), None)
        if prev_h is None or next_h is None:
            return False
        prev_row, next_row = rows[prev_h], rows[next_h]
        assert prev_row is not None and next_row is not None
        return min(prev_row, next_row) < row_score < max(prev_row, next_row)
