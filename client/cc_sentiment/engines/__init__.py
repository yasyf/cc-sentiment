from __future__ import annotations

from cc_sentiment.engines.claude_cli import (
    ClaudeCLIEngine,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    ClaudeStatus,
)
from cc_sentiment.engines.factory import ClaudeUnavailable, EngineFactory
from cc_sentiment.engines.filter import FRUSTRATION_PATTERN, matched_user_message, matches_frustration
from cc_sentiment.engines.filtered_engine import FilteredEngine
from cc_sentiment.engines.protocol import (
    DEFAULT_MODEL,
    NOOP_PROGRESS,
    NOOP_SNIPPET,
    InferenceEngine,
)

