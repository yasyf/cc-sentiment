from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from statistics import mean
from typing import ClassVar, Literal

from textual.app import App
from textual.widgets import Button, Digits, Label, ProgressBar, Static

from cc_sentiment.models import GistConfig, GPGConfig, MyStat, SentimentRecord, SSHConfig
from cc_sentiment.upload import UploadProgress

from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.progress import LiveFunStats, ScoringProgress
from cc_sentiment.tui.widgets import HourlyChart, SentimentPanel


@dataclass
class StatsState:
    total_buckets: int = 0
    total_sessions: int = 0
    total_files: int = 0
    rate: float = 0.0
    avg_score: float = 0.0
    toughest_hour: int | None = None
    toughest_day: int | None = None
    live_fun: LiveFunStats = field(default_factory=LiveFunStats)


@dataclass
class CtaState:
    SNAPSHOT_TITLE: ClassVar[str] = "Your cc-sentiment snapshot"
    TWEET_DETAIL: ClassVar[str] = (
        "We'll share a card with your GitHub avatar and this stat. "
        "Posts to Twitter; nothing else leaves your Mac."
    )
    TWEET_LABEL: ClassVar[str] = "Tweet it"
    SCHEDULE_TITLE: ClassVar[str] = "Keep this fresh every day?"
    SCHEDULE_DETAIL: ClassVar[str] = (
        "We'll re-scan your conversations and update your dashboard once a day in the background. "
        "Stop any time with [b]cc-sentiment uninstall[/]."
    )
    SCHEDULE_LABEL: ClassVar[str] = "Schedule daily"

    tweet_config: SSHConfig | GPGConfig | GistConfig | None = None
    tweet_stat: MyStat | None = None
    schedule_available: bool = False
    showing: Literal["tweet", "schedule"] = "schedule"
    active: bool = False

    @staticmethod
    def tweet_title(stat: MyStat) -> str:
        return f"You are {stat.text}."

    def has_tweet(self) -> bool:
        return self.tweet_config is not None and self.tweet_stat is not None

    def has_any(self) -> bool:
        return self.has_tweet() or self.schedule_available

    def advance(self) -> None:
        self.showing = "schedule" if self.showing == "tweet" else "tweet"

    def reset(self) -> None:
        self.tweet_config = None
        self.tweet_stat = None
        self.schedule_available = False
        self.showing = "schedule"
        self.active = False


class ProcessingView:
    WEEKDAY_LABELS: ClassVar[tuple[str, ...]] = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
    INSIGHTS_MIN_RECORDS: ClassVar[int] = 20
    INSIGHTS_MIN_SAMPLES: ClassVar[int] = 3
    LABEL_WIDTH: ClassVar[int] = 10

    def __init__(self, app: App[None]) -> None:
        self.app = app
        self.stats = StatsState()
        self.cta = CtaState()

    @staticmethod
    def append_line(widget: Static | Label, addition: str) -> None:
        existing = str(widget.render())
        widget.update(f"{existing}\n{addition}".strip())

    def update_status(self, text: str) -> None:
        self.app.query_one("#status-line", Label).update(text)

    def append_status(self, addition: str) -> None:
        self.append_line(self.app.query_one("#status-line", Label), addition)

    def reset(self) -> None:
        self.stats = StatsState()
        self.app.query_one("#scan-progress", ProgressBar).update(total=100, progress=0)
        self.app.query_one("#progress-label", Label).update("")
        self.app.query_one("#upload-progress", ProgressBar).update(total=100, progress=0)
        self.app.query_one("#upload-label", Label).update("")
        self.app.query_one("#progress-section").add_class("inactive")
        self.app.query_one("#scoring-row").add_class("inactive")
        self.app.query_one("#upload-row").add_class("inactive")
        self.app.query_one("#score-digits", Digits).update("-.--")
        self.app.query_one("#score-digits").add_class("inactive")
        self.app.query_one("#score-label").add_class("inactive")
        self.app.query_one("#hourly-chart", HourlyChart).update_chart([])
        self.app.query_one("#sentiment-panel", SentimentPanel).update_from_records([])
        self.app.query_one("#sentiment-section").add_class("inactive")
        self.app.query_one("#hourly-section").add_class("inactive")
        self.app.query_one("#stats-rows", Static).update("")
        self.app.query_one("#stats-section").add_class("inactive")
        self.reset_cta()

    def set_tweet(
        self, config: SSHConfig | GPGConfig | GistConfig, stat: MyStat
    ) -> None:
        self.cta.tweet_config = config
        self.cta.tweet_stat = stat
        if not self.cta.schedule_available:
            self.cta.showing = "tweet"
        self.render_cta()

    def set_schedule_available(self, available: bool) -> None:
        self.cta.schedule_available = available
        if not available and self.cta.showing == "schedule":
            self.cta.showing = "tweet"
        self.render_cta()

    def rotate_cta(self) -> None:
        if not (self.cta.has_tweet() and self.cta.schedule_available):
            return
        self.cta.advance()
        self.render_cta()

    def reset_cta(self) -> None:
        self.cta.reset()
        self.render_cta()

    def activate_cta(self) -> None:
        self.cta.active = True
        self.render_cta()

    def render_cta(self) -> None:
        section = self.app.query_one("#cta-section")
        if not (self.cta.has_any() and self.cta.active):
            section.add_class("inactive")
            return
        section.remove_class("inactive")
        title = self.app.query_one("#cta-title", Static)
        detail = self.app.query_one("#cta-detail", Static)
        button = self.app.query_one("#cta-action", Button)
        match self.cta.showing:
            case "tweet":
                assert self.cta.tweet_stat is not None
                title.update(CtaState.tweet_title(self.cta.tweet_stat))
                detail.update(CtaState.TWEET_DETAIL)
                button.label = CtaState.TWEET_LABEL
            case "schedule":
                title.update(CtaState.SCHEDULE_TITLE)
                detail.update(CtaState.SCHEDULE_DETAIL)
                button.label = CtaState.SCHEDULE_LABEL

    def begin_scoring(self, total: int, total_files: int) -> None:
        self.app.query_one("#scan-progress", ProgressBar).update(total=total, progress=0)
        self.app.query_one("#scoring-row").remove_class("inactive")
        self.app.query_one("#progress-section").remove_class("inactive")
        self.show_total_files(total_files)

    def show_total_files(self, total_files: int) -> None:
        self.stats.total_files = total_files
        self.render_stats()

    def update_progress_label(self, scoring: ScoringProgress, scored: int, total: int) -> None:
        elapsed = scoring.elapsed()
        projected = scoring.projected_total(scored, total)
        self.app.query_one("#progress-label", Label).update(
            f"[b]{TimeFormat.format_hms(elapsed)}[/] / [dim]~{TimeFormat.format_hms(projected)}[/]"
        )

    def bump_scored(self, scored: int, scoring: ScoringProgress) -> None:
        self.app.query_one("#scan-progress", ProgressBar).update(progress=scored)
        self.stats.rate = scoring.rate(scored)
        self.render_stats()

    def update_upload(self, progress: UploadProgress) -> None:
        section = self.app.query_one("#progress-section")
        row = self.app.query_one("#upload-row")
        bar = self.app.query_one("#upload-progress", ProgressBar)
        label = self.app.query_one("#upload-label", Label)
        if progress.queued_records == 0:
            row.add_class("inactive")
            return
        section.remove_class("inactive")
        row.remove_class("inactive")
        total = max(progress.queued_records, 1)
        bar.update(total=total, progress=min(progress.uploaded_records, total))
        label.update(
            f"[b cyan]{progress.uploaded_records:,}[/]/[b cyan]{progress.queued_records:,}[/] "
            "[dim]moments[/]"
        )

    def show_stats(self, buckets: int, sessions: int, files: int) -> None:
        self.stats.total_buckets = buckets
        self.stats.total_sessions = sessions
        self.stats.total_files = files
        self.render_stats()

    def hide_moments(self) -> None:
        self.app.query_one("#moments-section").add_class("inactive")

    def track_frustration(self, words: list[str]) -> None:
        self.stats.live_fun.bump(words)
        self.render_stats()

    def render_scores(self, records: list[SentimentRecord]) -> None:
        if not records:
            return
        self.app.query_one("#sentiment-section").remove_class("inactive")
        self.app.query_one("#hourly-section").remove_class("inactive")
        self.app.query_one("#score-digits").remove_class("inactive")
        self.app.query_one("#score-label").remove_class("inactive")
        scores = [int(r.sentiment_score) for r in records]
        avg = mean(scores)
        self.app.query_one("#score-digits", Digits).update(f"{avg:.2f}")
        self.app.query_one("#sentiment-panel", SentimentPanel).update_from_records(records)
        self.app.query_one("#hourly-chart", HourlyChart).update_chart(records)
        sessions = len({r.conversation_id for r in records})
        self.stats.total_buckets = len(scores)
        self.stats.total_sessions = sessions
        self.stats.avg_score = avg
        self.update_peaks(records)
        self.render_stats()

    @staticmethod
    def pick_toughest[K](groups: dict[K, list[int]], min_samples: int) -> K | None:
        qualifying = {k: mean(v) for k, v in groups.items() if len(v) >= min_samples}
        return min(qualifying, key=qualifying.__getitem__) if qualifying else None

    def update_peaks(self, records: list[SentimentRecord]) -> None:
        if len(records) < self.INSIGHTS_MIN_RECORDS:
            self.stats.toughest_hour = None
            self.stats.toughest_day = None
            return
        hours: dict[int, list[int]] = defaultdict(list)
        days: dict[int, list[int]] = defaultdict(list)
        for r in records:
            local = r.time.astimezone()
            score = int(r.sentiment_score)
            hours[local.hour].append(score)
            days[local.weekday()].append(score)
        self.stats.toughest_hour = self.pick_toughest(hours, self.INSIGHTS_MIN_SAMPLES)
        self.stats.toughest_day = self.pick_toughest(days, self.INSIGHTS_MIN_SAMPLES)

    def render_stats(self) -> None:
        section = self.app.query_one("#stats-section")
        if self.stats.total_buckets == 0:
            section.add_class("inactive")
            self.app.query_one("#stats-rows", Static).update("")
            return
        section.remove_class("inactive")
        lines: list[str] = [self.stats_row("venting", self.venting_phrase())]
        volume_parts = [
            f"[b cyan]{self.stats.total_buckets:,}[/] moments",
            f"[b cyan]{self.stats.total_sessions:,}[/] chats",
            f"[b cyan]{self.stats.total_files:,}[/] transcripts",
        ]
        lines.append(self.stats_row("volume", " · ".join(volume_parts)))
        if self.stats.rate > 0:
            lines.append(self.stats_row(
                "pace",
                f"[b cyan]{self.stats.rate:.1f}[/] [dim]moments per second on this Mac[/]",
            ))
        peaks = self.peaks_phrase()
        if peaks:
            lines.append(self.stats_row("lows", peaks))
        self.app.query_one("#stats-rows", Static).update("\n".join(lines))

    @classmethod
    def stats_row(cls, label: str, value: str) -> str:
        return f"[dim]{label:<{cls.LABEL_WIDTH}}[/] {value}"

    def venting_phrase(self) -> str:
        top = self.stats.live_fun.top()
        if top is None:
            return "[dim]you're kind to Claude[/]"
        word, count = top
        return f'[b yellow]"{word}"[/] ×[b cyan]{count}[/]'

    def peaks_phrase(self) -> str:
        parts: list[str] = []
        match (self.stats.toughest_hour, self.stats.toughest_day):
            case (int() as h, int() as d):
                parts.append(
                    f"[b red]{TimeFormat.format_hour(h)}[/] on [b red]{self.WEEKDAY_LABELS[d]}[/]"
                )
            case (int() as h, None):
                parts.append(f"[b red]{TimeFormat.format_hour(h)}[/]")
            case (None, int() as d):
                parts.append(f"on [b red]{self.WEEKDAY_LABELS[d]}[/]")
            case _:
                pass
        return " · ".join(parts)
