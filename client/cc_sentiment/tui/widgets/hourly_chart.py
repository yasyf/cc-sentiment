from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import ClassVar

from textual.widgets import Static

from cc_sentiment.models import SentimentRecord


class HourlyChart(Static):
    DEFAULT_CSS = """
    HourlyChart { height: 7; }
    """

    COLORS: ClassVar[dict[int, str]] = {1: "red", 2: "dark_orange", 3: "yellow", 4: "green", 5: "cyan"}
    Y_TICKS: ClassVar[dict[int, str]] = {5: "😄", 4: "🙂", 3: "😐", 2: "😕", 1: "😡"}
    X_LABELS: ClassVar[dict[int, str]] = {0: "12a", 6: "6a", 12: "12p", 18: "6p", 23: "11p"}
    WINDOW_DAYS: ClassVar[int] = 30

    def update_chart(self, records: list[SentimentRecord]) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS)
        by_hour: dict[int, list[int]] = defaultdict(list)
        for r in records:
            if r.time >= cutoff:
                by_hour[r.time.astimezone().hour].append(int(r.sentiment_score))

        rows: list[int | None] = [
            max(1, min(5, round(mean(scores)))) if (scores := by_hour.get(h)) else None
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
