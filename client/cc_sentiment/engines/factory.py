from __future__ import annotations

import platform
import sys
from functools import partial

import anyio.to_thread

from cc_sentiment.engines.claude_cli import ClaudeCLIEngine, ClaudeReady, ClaudeStatus
from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.protocol import DEFAULT_MODEL, InferenceEngine


class ClaudeUnavailable(Exception):
    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__(repr(status))
        self.status = status


class EngineFactory:
    @classmethod
    def default(cls) -> str:
        match (sys.platform, platform.machine()):
            case ("darwin", "arm64"):
                return "mlx"
            case _:
                return "claude"

    @classmethod
    def resolve(cls, requested: str | None) -> str:
        engine = requested or cls.default()
        if engine != "claude":
            return engine
        match ClaudeCLIEngine.check_status():
            case ClaudeReady():
                return engine
            case status:
                raise ClaudeUnavailable(status)

    @classmethod
    async def build(cls, kind: str, model_repo: str | None = None) -> InferenceEngine:
        match kind:
            case "mlx":
                from cc_sentiment.sentiment import AdapterFuser, SentimentClassifier
                fused_dir = await anyio.to_thread.run_sync(
                    AdapterFuser.ensure_fused, model_repo or DEFAULT_MODEL
                )
                inner: InferenceEngine = await anyio.to_thread.run_sync(
                    partial(SentimentClassifier, fused_dir)
                )
            case "claude":
                inner = ClaudeCLIEngine(model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            case _:
                raise ValueError(f"Unknown engine: {kind}")
        return FrustrationFilter(inner)
