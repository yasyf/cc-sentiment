from __future__ import annotations

from cc_sentiment.tui.dashboard.format import ScoreEmoji, TimeFormat
from cc_sentiment.tui.dashboard.moments_view import MomentsView
from cc_sentiment.tui.dashboard.popovers import BootingScreen, StatShareScreen
from cc_sentiment.tui.dashboard.popovers.stat_share import CardFetcher
from cc_sentiment.tui.dashboard.progress import (
    DebugState,
    LiveFunStats,
    ScoringProgress,
)
from cc_sentiment.tui.dashboard.screen import DashboardScreen
from cc_sentiment.tui.dashboard.stages import (
    Authenticating,
    Booting,
    Discovering,
    Error,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)
from cc_sentiment.tui.dashboard.view import CtaState, ProcessingView
