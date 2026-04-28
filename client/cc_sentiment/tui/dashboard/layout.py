from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Digits, Footer, Header, Label, Static

from cc_sentiment.models import CLIENT_VERSION

from cc_sentiment.tui.dashboard.widgets import (
    DebugSection,
    HourlyChart,
    ProgressRow,
    SentimentPanel,
)
from cc_sentiment.tui.widgets import Card

__all__ = ["DashboardLayout"]


class DashboardLayout:
    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            with Vertical(id="header-section"):
                with Horizontal(id="title-row"):
                    yield Static(f"[b]cc-sentiment[/b] [dim]v{CLIENT_VERSION}[/]", id="title-text")
                    yield Digits("-.--", id="score-digits", classes="inactive")
                yield Static("[dim]your average score[/]", id="score-label", classes="inactive")

            with Card(id="progress-section", title="progress", classes="inactive"):
                yield ProgressRow(
                    label="scoring",
                    bar_id="scan-progress",
                    context_id="progress-label",
                    id="scoring-row",
                    classes="inactive",
                )
                yield ProgressRow(
                    label="uploading",
                    bar_id="upload-progress",
                    context_id="upload-label",
                    id="upload-row",
                    classes="inactive",
                )

            with Horizontal(classes="row"):
                with Card(id="sentiment-section", title="how it feels", classes="inactive"):
                    yield SentimentPanel(id="sentiment-panel")
                with Card(id="hourly-section", title="through the day", classes="inactive"):
                    yield HourlyChart(id="hourly-chart")

            with Horizontal(classes="row"):
                with Card(id="moments-section", title="moments", classes="inactive"):
                    yield Static("", id="moments-log")
                with Card(id="stats-section", title="your numbers", classes="inactive"):
                    yield Static("", id="stats-rows")
                with Card(id="cta-section", title="", classes="inactive"):
                    yield Static("", id="cta-title")
                    yield Static("", id="cta-detail")
                    with Horizontal(id="cta-buttons"):
                        yield Button("", id="cta-action", variant="primary")

            if self.debug_mode:
                yield DebugSection(id="debug")

            yield Label("", id="status-line")
        yield Footer()
