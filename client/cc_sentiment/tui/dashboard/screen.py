from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from textual import on
from textual.binding import Binding
from textual.reactive import reactive
from textual.screen import Screen
from textual.widgets import Button

from cc_sentiment.model_cache import ModelCache
from cc_sentiment.models import AppState, SentimentRecord
from cc_sentiment.pipeline import ScanCache
from cc_sentiment.repo import Repository
from cc_sentiment.upload import UploadProgress

from cc_sentiment.tui.dashboard.actions import DashboardActions
from cc_sentiment.tui.dashboard.flow import DashboardFlow
from cc_sentiment.tui.dashboard.layout import DashboardLayout
from cc_sentiment.tui.dashboard.lifecycle import DashboardLifecycle
from cc_sentiment.tui.dashboard.popovers import BootingScreen
from cc_sentiment.tui.dashboard.presenter import DashboardStagePresenter
from cc_sentiment.tui.dashboard.progress import DebugState, ScoringProgress
from cc_sentiment.tui.dashboard.stages import Booting, Stage
from cc_sentiment.tui.dashboard.view import ProcessingView

__all__ = ["DashboardScreen"]


class DashboardScreen(
    DashboardLayout,
    DashboardLifecycle,
    DashboardStagePresenter,
    DashboardFlow,
    DashboardActions,
    Screen[None],
):
    RESCAN_CONFIRM_SECONDS: ClassVar[float] = 5.0
    CTA_ROTATE_SECONDS: ClassVar[float] = 10.0
    AUTO_OPEN_DASHBOARD_DELAY_SECONDS: ClassVar[float] = 3.0

    DEFAULT_CSS = """
    DashboardScreen { layout: vertical; background: $surface; }
    DashboardScreen #main { height: 1fr; padding: 1 2; }
    DashboardScreen #header-section { height: auto; }
    DashboardScreen #title-row { height: 3; }
    DashboardScreen #title-text { width: 1fr; }
    DashboardScreen #score-digits { width: auto; min-width: 20; color: $accent; }
    DashboardScreen #score-label { text-align: right; height: 1; color: $text-muted; }
    DashboardScreen #score-digits.inactive,
    DashboardScreen #score-label.inactive { display: none; }
    DashboardScreen .row { height: auto; }
    DashboardScreen .row > Card { margin-right: 1; }
    DashboardScreen .row > Card:last-of-type { margin-right: 0; }
    DashboardScreen #sentiment-section { width: 2fr; }
    DashboardScreen #hourly-section { width: 1fr; min-width: 32; }
    DashboardScreen #moments-section { width: 2fr; }
    DashboardScreen #stats-section { width: 1fr; min-width: 36; }
    DashboardScreen #cta-section { width: 1fr; min-width: 36; }
    DashboardScreen #cta-title { color: $accent; text-style: bold; margin: 0 0 1 0; }
    DashboardScreen #cta-detail { color: $text-muted; margin: 0 0 1 0; }
    DashboardScreen #cta-buttons { height: auto; }
    DashboardScreen ProgressBar Bar > .bar--bar { color: $accent; }
    DashboardScreen ProgressBar Bar > .bar--complete { color: $accent; }
    DashboardScreen #hourly-chart { height: 5; }
    DashboardScreen #moments-log {
        height: auto; min-height: 4; max-height: 10; color: $foreground;
    }
    DashboardScreen #stats-rows { height: auto; }
    DashboardScreen #status-line { height: auto; margin: 1 0 0 0; }
    DashboardScreen Button.-primary:focus,
    DashboardScreen Button.-default:focus { text-style: bold; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("escape", "quit", "Quit"),
        Binding("r", "rescan", "Rescan"),
        Binding("o", "open_dashboard", "Open stats"),
    ]

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")
    stage: reactive[Stage] = reactive(Booting())
    debug_state: reactive[DebugState | None] = reactive(None)

    def __init__(
        self,
        state: AppState,
        model_repo: str | None = None,
        db_path: Path | None = None,
        debug: bool = False,
    ) -> None:
        Screen.__init__(self)
        self.state = state
        self.model_repo = model_repo
        self.db_path = db_path or Repository.default_path()
        self.debug_mode = debug
        self.repo: Repository | None = None
        self.scan_cache: ScanCache | None = None
        self.records: list[SentimentRecord] = []
        self.view: ProcessingView | None = None
        self._scoring = ScoringProgress()
        self._upload = UploadProgress()
        self._debug_state = DebugState()
        self._boot_screen: BootingScreen | None = None
        self._model_cache = ModelCache()
        self.engine: str | None = None
        self._auto_swapped_to_claude = False

    @on(Button.Pressed, "#cta-action")
    async def on_cta_action(self) -> None:
        await self._handle_cta_action()
