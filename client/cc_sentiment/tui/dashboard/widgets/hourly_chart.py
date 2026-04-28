# DELETE_AFTER_SCREENS: Dashboard-specific composite — 24-hour bar chart
# tied to SentimentRecord. Move next to the dashboard screen file when the
# dashboard is extracted from app.py into its own screen.
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from statistics import mean
from typing import ClassVar

from textual.widgets import Static

from cc_sentiment.models import SentimentRecord
from cc_sentiment.tui.dashboard.format import ScoreEmoji, TimeFormat


@dataclass(frozen=True)
class ToughHour:
    hour: int
    avg_score: float
    count: int


class HourlyChart(Static):
    DEFAULT_CSS: ClassVar[str] = """
    HourlyChart { height: 5; }
    """

    SCORE_TOKENS: ClassVar[dict[int, str]] = {
        1: "$error",
        2: "$error",
        3: "$warning",
        4: "$success",
        5: "$success",
    }
    BAR_LEVELS: ClassVar[tuple[str, ...]] = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
    EMPTY_CELL: ClassVar[str] = "[$text-muted]·[/]"
    X_LABELS: ClassVar[dict[int, str]] = {0: "12a", 6: "6a", 12: "12p", 18: "6p", 23: "11p"}
    WINDOW_DAYS: ClassVar[int] = 30
    UCB_MIN_SAMPLES: ClassVar[int] = 5
    UCB_Z: ClassVar[float] = 1.645
    UCB_SIGMA: ClassVar[float] = 0.7
    HOURS: ClassVar[int] = 24

    def update_chart(self, records: list[SentimentRecord]) -> None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.WINDOW_DAYS)
        scores_by_hour: dict[int, list[int]] = defaultdict(list)
        for r in records:
            if r.time >= cutoff:
                scores_by_hour[r.time.astimezone().hour].append(int(r.sentiment_score))

        if not scores_by_hour:
            self.update("[$text-muted]no data yet — keep using Claude Code[/]")
            return

        counts = [len(scores_by_hour.get(h, [])) for h in range(self.HOURS)]
        max_count = max(counts)
        bar_row = "".join(self.cell(scores_by_hour.get(h), max_count) for h in range(self.HOURS))
        axis_line = "[$text-muted]" + "─" * self.HOURS + "[/]"
        caption = self.caption(scores_by_hour)
        self.update("\n".join([caption, "", bar_row, axis_line, self.axis_labels()]))

    @classmethod
    def cell(cls, scores: list[int] | None, max_count: int) -> str:
        if not scores:
            return cls.EMPTY_CELL
        steps = len(cls.BAR_LEVELS) - 1
        level = max(1, round(len(scores) / max_count * steps))
        return f"[{cls.SCORE_TOKENS[round(mean(scores))]}]{cls.BAR_LEVELS[level]}[/]"

    @classmethod
    def caption(cls, scores_by_hour: dict[int, list[int]]) -> str:
        if (tough := cls.toughest_hour(scores_by_hour)) is None:
            return ""
        return (
            f"[$text-muted]your tough hour:[/] "
            f"[b $error]{TimeFormat.format_hour_short(tough.hour)}[/] "
            f"[$text-muted]({tough.avg_score:.1f} {ScoreEmoji.for_avg(tough.avg_score)})[/]"
        )

    @classmethod
    def toughest_hour(cls, scores_by_hour: dict[int, list[int]]) -> ToughHour | None:
        eligible = [
            ToughHour(h, mean(scores), len(scores))
            for h, scores in scores_by_hour.items()
            if len(scores) >= cls.UCB_MIN_SAMPLES
        ]
        if not eligible:
            return None
        return min(
            eligible,
            key=lambda t: t.avg_score + cls.UCB_Z * cls.UCB_SIGMA / math.sqrt(t.count),
        )

    @classmethod
    def axis_labels(cls) -> str:
        buf = list(" " * cls.HOURS)
        for h, lbl in cls.X_LABELS.items():
            for i, ch in enumerate(lbl):
                if h + i < cls.HOURS:
                    buf[h + i] = ch
        return "[$text-muted]" + "".join(buf).rstrip() + "[/]"
