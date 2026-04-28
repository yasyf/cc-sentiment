# DELETE_AFTER_SCREENS: Dashboard-specific composite — histogram + stat line
# tied to SentimentRecord. Move next to the dashboard screen file when the
# dashboard is extracted from app.py into its own screen.
from __future__ import annotations

from collections import Counter
from statistics import mean
from typing import ClassVar

from textual.widgets import Static

from cc_sentiment.models import SentimentRecord
from cc_sentiment.tui.dashboard.format import ScoreEmoji


class SentimentPanel(Static):
    DEFAULT_CSS: ClassVar[str] = """
    SentimentPanel { height: auto; }
    """

    SCORES: ClassVar[tuple[int, ...]] = (1, 2, 3, 4, 5)
    SCORE_TOKENS: ClassVar[dict[int, str]] = {
        1: "$error",
        2: "$error",
        3: "$warning",
        4: "$success",
        5: "$success",
    }
    BAR_LEVELS: ClassVar[tuple[str, ...]] = (" ", "▁", "▂", "▃", "▄", "▅", "▆", "▇", "█")
    BAR_ROWS: ClassVar[int] = 4
    EMPTY_TEXT: ClassVar[str] = "[$text-muted]warming up — no scores yet[/]"

    def update_from_records(self, records: list[SentimentRecord]) -> None:
        if not records:
            self.update(self.EMPTY_TEXT)
            return
        scores = [int(r.sentiment_score) for r in records]
        counts = Counter(scores)
        total = len(scores)
        sessions = len({r.conversation_id for r in records})
        avg = mean(scores)
        frustrated_pct = 100 * sum(c for s, c in counts.items() if s <= 2) / total

        stat_line = (
            f"[b]{avg:.1f}[/] {ScoreEmoji.for_avg(avg)}"
            f"  [$text-muted]·[/]  "
            f"[b $error]{frustrated_pct:.0f}%[/] [$text-muted]frustrated[/]"
            f"  [$text-muted]·[/]  "
            f"[b]{sessions:,}[/] [$text-muted]chats[/]"
        )
        bar_lines = self.render_histogram(counts, max(counts.values()))
        emojis = "".join(f"{ScoreEmoji.for_score(s)}   " for s in self.SCORES)
        pcts = "[$text-muted]" + "".join(
            f"{counts.get(s, 0) * 100 / total:>3.0f}% " for s in self.SCORES
        ) + "[/]"

        self.update("\n".join([stat_line, "", *bar_lines, emojis, pcts]))

    @classmethod
    def render_histogram(cls, counts: Counter[int], max_count: int) -> list[str]:
        steps = len(cls.BAR_LEVELS) - 1
        return [
            "".join(
                cls.render_cell(s, counts.get(s, 0), max_count, (row - 1) * steps, steps)
                for s in cls.SCORES
            )
            for row in range(cls.BAR_ROWS, 0, -1)
        ]

    @classmethod
    def render_cell(cls, score: int, count: int, max_count: int, lo: int, steps: int) -> str:
        level = round(count / max_count * cls.BAR_ROWS * steps)
        ch = cls.BAR_LEVELS[max(0, min(steps, level - lo))]
        return f"[{cls.SCORE_TOKENS[score]}] {ch}   [/]"
