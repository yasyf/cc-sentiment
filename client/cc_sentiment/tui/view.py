from __future__ import annotations

from collections import Counter, defaultdict
from statistics import mean
from typing import ClassVar

from textual.app import App
from textual.widgets import Digits, Label, ProgressBar, Static

from cc_sentiment.models import SentimentRecord
from cc_sentiment.upload import UploadProgress

from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.progress import LiveFunStats, ScoringProgress
from cc_sentiment.tui.widgets import HourlyChart, LiveFunBox, ScoreBar


class ProcessingView:
    WEEKDAY_LABELS: ClassVar[tuple[str, ...]] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    INSIGHTS_MIN_RECORDS: ClassVar[int] = 20
    INSIGHTS_MIN_SAMPLES: ClassVar[int] = 3

    def __init__(self, app: App[None]) -> None:
        self.app = app
        self.score_bars: dict[int, ScoreBar] = {}

    def register_score_bar(self, s: int, bar: ScoreBar) -> None:
        self.score_bars[s] = bar

    @staticmethod
    def append_line(widget: Static | Label, addition: str) -> None:
        existing = str(widget.render())
        widget.update(f"{existing}\n{addition}".strip())

    def update_status(self, text: str) -> None:
        self.app.query_one("#status-line", Label).update(text)

    def append_status(self, addition: str) -> None:
        self.append_line(self.app.query_one("#status-line", Label), addition)

    def reset(self) -> None:
        self.app.query_one("#scan-progress", ProgressBar).update(total=100, progress=0)
        self.app.query_one("#progress-label", Label).update("Preparing...")
        self.app.query_one("#score-digits", Digits).update("-.--")
        self.app.query_one("#hourly-chart", HourlyChart).update_chart([])
        for s in range(1, 6):
            self.score_bars[s].update("")
        for stat_id in ("#stat-buckets", "#stat-sessions", "#stat-files", "#stat-rate"):
            self.app.query_one(stat_id, Static).update("--")
        self.app.query_one("#chart").add_class("inactive")
        self.app.query_one("#stats").add_class("inactive")
        self.app.query_one("#score-digits").add_class("inactive")
        self.app.query_one("#score-label").add_class("inactive")
        self.app.query_one("#upload").add_class("inactive")
        self.app.query_one("#upload-progress", ProgressBar).update(total=100, progress=0)
        self.app.query_one("#upload-label", Label).update("")
        self.app.query_one("#stats-insights").add_class("inactive")

    def begin_scoring(self, total: int, total_files: int) -> None:
        self.app.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self.show_total_files(total_files)

    def show_total_files(self, total_files: int) -> None:
        self.app.query_one("#stat-files", Static).update(f"[b]{total_files:,}[/]")
        self.app.query_one("#stats").remove_class("inactive")

    def update_progress_label(self, scoring: ScoringProgress, scored: int, total: int) -> None:
        elapsed = scoring.elapsed()
        projected = scoring.projected_total(scored, total)
        self.app.query_one("#progress-label", Label).update(
            f"[b]{TimeFormat.format_hms(elapsed)}[/] / ~{TimeFormat.format_hms(projected)}"
        )

    def bump_scored(self, scored: int, scoring: ScoringProgress, total: int) -> None:
        self.app.query_one("#scan-progress", ProgressBar).update(progress=scored)
        self.update_progress_label(scoring, scored, total)
        self.app.query_one("#stat-rate", Static).update(f"{scoring.rate(scored):.1f}")

    def update_upload(self, progress: UploadProgress) -> None:
        section = self.app.query_one("#upload")
        bar = self.app.query_one("#upload-progress", ProgressBar)
        label = self.app.query_one("#upload-label", Label)
        if progress.queued_records == 0:
            section.add_class("inactive")
            return
        section.remove_class("inactive")
        total = max(progress.queued_records, 1)
        bar.update(total=total, progress=min(progress.uploaded_records, total))
        label.update(
            f"[dim]Uploading to sentiments.cc · [b]{progress.uploaded_records:,}[/]"
            f"/[b]{progress.queued_records:,}[/] moments[/]"
        )

    def show_stats(self, buckets: int, sessions: int, files: int) -> None:
        self.app.query_one("#stat-buckets", Static).update(f"[b]{buckets:,}[/]")
        self.app.query_one("#stat-sessions", Static).update(f"[b]{sessions:,}[/]")
        self.app.query_one("#stat-files", Static).update(f"[b]{files:,}[/]")
        self.app.query_one("#stats").remove_class("inactive")

    def hide_engine_boot(self) -> None:
        self.app.query_one("#engine-boot").add_class("inactive")

    def update_live_fun(self, stats: LiveFunStats) -> None:
        self.app.query_one("#live-fun", LiveFunBox).render_stats(stats)

    def render_scores(self, records: list[SentimentRecord]) -> None:
        if not records:
            return
        self.app.query_one("#chart").remove_class("inactive")
        self.app.query_one("#score-digits").remove_class("inactive")
        self.app.query_one("#score-label").remove_class("inactive")
        scores = [int(r.sentiment_score) for r in records]
        counts = Counter(scores)
        total = len(scores)
        max_count = max(counts.values()) if counts else 1
        for s in range(1, 6):
            n = counts.get(s, 0)
            self.score_bars[s].update(self.score_bars[s].render_bar(n, total, max_count))
        avg = mean(scores)
        self.app.query_one("#score-digits", Digits).update(f"{avg:.2f}")
        self.app.query_one("#hourly-chart", HourlyChart).update_chart(records)
        sessions = len({r.conversation_id for r in records})
        self.app.query_one("#stat-buckets", Static).update(f"[b]{total:,}[/]")
        self.app.query_one("#stat-sessions", Static).update(f"[b]{sessions:,}[/]")
        self.render_insights(records)

    @staticmethod
    def pick_toughest[K](groups: dict[K, list[int]], min_samples: int) -> K | None:
        qualifying = {k: mean(v) for k, v in groups.items() if len(v) >= min_samples}
        return min(qualifying, key=qualifying.__getitem__) if qualifying else None

    @staticmethod
    def short_model(model: str) -> str:
        return next(
            (t for t in model.split("-") if t not in ("claude", "anthropic") and not t.isdigit()),
            model,
        )

    def render_insights(self, records: list[SentimentRecord]) -> None:
        insights = self.app.query_one("#stats-insights", Static)
        if len(records) < self.INSIGHTS_MIN_RECORDS:
            insights.add_class("inactive")
            return
        hours: dict[int, list[int]] = defaultdict(list)
        days: dict[int, list[int]] = defaultdict(list)
        models: dict[str, list[int]] = defaultdict(list)
        for r in records:
            local = r.time.astimezone()
            score = int(r.sentiment_score)
            hours[local.hour].append(score)
            days[local.weekday()].append(score)
            models[r.claude_model].append(score)
        parts: list[str] = []
        if (h := self.pick_toughest(hours, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest hour:[/] [b]{TimeFormat.format_hour(h)}[/]")
        if (d := self.pick_toughest(days, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest day:[/] [b]{self.WEEKDAY_LABELS[d]}[/]")
        if (m := self.pick_toughest(models, self.INSIGHTS_MIN_SAMPLES)) is not None:
            parts.append(f"[dim]toughest model:[/] [b]{self.short_model(m)}[/]")
        if not parts:
            insights.add_class("inactive")
            return
        insights.update(" · ".join(parts))
        insights.remove_class("inactive")
