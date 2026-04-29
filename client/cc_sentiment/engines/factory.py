from __future__ import annotations

import platform
import sys
import threading

import anyio.to_thread

from cc_sentiment.engines.claude_cli import ClaudeCLIEngine, ClaudeReady, ClaudeStatus
from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.filtered_engine import FilteredEngine
from cc_sentiment.engines.imperative_filter import ImperativeMildIrritationFilter
from cc_sentiment.engines.positive_clamp_filter import PositiveClampFilter
from cc_sentiment.engines.protocol import DEFAULT_MODEL, InferenceEngine
from cc_sentiment.engines.score_filter import ScoreFilter
from cc_sentiment.engines.session_resume_filter import SessionResumeFilter
from cc_sentiment.hardware import Hardware

DEFAULT_FILTERS: tuple[ScoreFilter, ...] = (
    FrustrationFilter(),
    PositiveClampFilter(),
    ImperativeMildIrritationFilter(),
    SessionResumeFilter(),
)


class ClaudeUnavailable(Exception):
    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__(repr(status))
        self.status = status


class EngineFactory:
    @staticmethod
    def configure_hub_progress() -> None:
        from huggingface_hub.utils.tqdm import tqdm as hf_tqdm
        hf_tqdm.set_lock(threading.RLock())

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
        if (
            requested is None
            and engine == "mlx"
            and Hardware.read_free_memory_gb() < Hardware.LOW_RAM_THRESHOLD_GB
            and isinstance(ClaudeCLIEngine.check_status(), ClaudeReady)
        ):
            engine = "claude"
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
                cls.configure_hub_progress()
                from cc_sentiment.sentiment import AdapterFuser, SentimentClassifier
                fused_dir = await anyio.to_thread.run_sync(
                    AdapterFuser.ensure_fused, model_repo or DEFAULT_MODEL
                )
                classifier = SentimentClassifier(fused_dir)
                await classifier.ensure_loaded()
                inner: InferenceEngine = classifier
            case "claude":
                inner = ClaudeCLIEngine(model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            case _:
                raise ValueError(f"Unknown engine: {kind}")
        return FilteredEngine(inner, DEFAULT_FILTERS)
