from __future__ import annotations

import importlib.util
import platform
import sys
from collections.abc import Callable

import anyio.to_thread

from cc_sentiment.engines.claude_cli import ClaudeCLIEngine
from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.omlx import OMLXEngine
from cc_sentiment.engines.protocol import DEFAULT_MODEL, InferenceEngine


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
        if engine != "claude" or ClaudeCLIEngine.is_available():
            return engine
        raise RuntimeError(
            "Can't run sentiment analysis on this platform.\n"
            "cc-sentiment needs Apple Silicon for local inference, "
            "or the `claude` CLI as a fallback.\n\n"
            "Install Claude Code from https://claude.com/claude-code, "
            "then run `claude auth login` and try again."
        )

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
                    SentimentClassifier, model_repo or DEFAULT_MODEL
                )
            case "omlx":
                omlx = await anyio.to_thread.run_sync(OMLXEngine, model_repo, on_engine_log)
                await omlx.warm_system_prompt()
                inner = omlx
            case "claude":
                inner = ClaudeCLIEngine(model=model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            case _:
                raise ValueError(f"Unknown engine: {kind}")
        return FrustrationFilter(inner)
