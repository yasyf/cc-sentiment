from __future__ import annotations

import importlib.util
import platform
import sys
from collections.abc import Callable
from functools import partial

import anyio.to_thread

from cc_sentiment.engines.claude_cli import ClaudeCLIEngine, ClaudeReady, ClaudeStatus
from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.omlx import OMLXEngine
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
                return "omlx"
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
    async def build(
        cls,
        kind: str,
        model_repo: str | None = None,
        on_engine_log: Callable[[str], None] | None = None,
    ) -> InferenceEngine:
        match kind:
            case "mlx":
                if importlib.util.find_spec("mlx_lm") is None:
                    raise RuntimeError(
                        "The local mlx engine needs the `mlx` extra. "
                        "Install with `uvx 'cc-sentiment[mlx]'` (Apple Silicon only), "
                        "or use the default engine instead."
                    )
                from cc_sentiment.sentiment import SentimentClassifier
                inner: InferenceEngine = await anyio.to_thread.run_sync(
                    partial(SentimentClassifier, model_repo or DEFAULT_MODEL)
                )
            case "omlx":
                omlx = await anyio.to_thread.run_sync(
                    partial(OMLXEngine, model_repo, on_engine_log)
                )
                await omlx.warm_system_prompt()
                inner = omlx
            case "claude":
                inner = ClaudeCLIEngine(model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            case _:
                raise ValueError(f"Unknown engine: {kind}")
        return FrustrationFilter(inner)
