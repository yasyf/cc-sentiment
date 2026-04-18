from __future__ import annotations

from cc_sentiment.engines.claude_cli import ClaudeCLIEngine
from cc_sentiment.engines.factory import EngineFactory
from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.omlx import OMLXEngine, SILENT_LOG
from cc_sentiment.engines.protocol import (
    DEFAULT_MODEL,
    NOOP_PROGRESS,
    NOOP_SNIPPET,
    STRUCTURED_OUTPUTS_CHOICE,
    SYSTEM_PROMPT,
    InferenceEngine,
)

__all__ = [
    "DEFAULT_MODEL",
    "NOOP_PROGRESS",
    "NOOP_SNIPPET",
    "SILENT_LOG",
    "STRUCTURED_OUTPUTS_CHOICE",
    "SYSTEM_PROMPT",
    "ClaudeCLIEngine",
    "EngineFactory",
    "FrustrationFilter",
    "InferenceEngine",
    "OMLXEngine",
]
