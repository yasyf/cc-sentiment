from __future__ import annotations

import asyncio
import contextlib
import functools
import io
import threading
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Any, Literal

import anyio
import anyio.to_thread
from tqdm import tqdm as TqdmBase

from cc_sentiment.engines.base import BaseEngine

__all__ = ["ModelCache", "ModelLoadProgress", "ModelLoadState"]


ModelLoadState = Literal["idle", "downloading", "loading", "ready", "failed"]


@dataclass
class ModelLoadProgress:
    state: ModelLoadState = "idle"
    bytes_downloaded: int = 0
    bytes_total: int = 0
    error: str | None = None


@dataclass
class ModelCache:
    progress: ModelLoadProgress = field(default_factory=ModelLoadProgress)
    classifier: BaseEngine | None = None
    _lock: anyio.Lock = field(default_factory=anyio.Lock)
    _byte_lock: threading.Lock = field(default_factory=threading.Lock)
    _task: asyncio.Task[BaseEngine] | None = None
    _listeners: list[Callable[[ModelLoadProgress], None]] = field(default_factory=list)

    def subscribe(self, on_change: Callable[[ModelLoadProgress], None]) -> Callable[[], None]:
        self._listeners.append(on_change)
        on_change(replace(self.progress))
        return functools.partial(self._unsubscribe, on_change)

    def _unsubscribe(self, on_change: Callable[[ModelLoadProgress], None]) -> None:
        with contextlib.suppress(ValueError):
            self._listeners.remove(on_change)

    async def ensure_started(self, model_repo: str) -> None:
        async with self._lock:
            if self._task is not None:
                return
            self._task = asyncio.create_task(self._load(model_repo), name="model-load")

    async def get(self) -> BaseEngine:
        if self._task is None:
            raise RuntimeError("ModelCache.get() called before ensure_started()")
        await self._task
        assert self.classifier is not None
        return self.classifier

    def invalidate(self) -> None:
        self.classifier = None
        self.progress = ModelLoadProgress()
        self._task = None

    def _emit(self) -> None:
        snap = replace(self.progress)
        for listener in list(self._listeners):
            listener(snap)

    def _on_byte_delta(self, n: int) -> None:
        with self._byte_lock:
            self.progress.bytes_downloaded += n
        self._emit()

    async def _load(self, model_repo: str) -> BaseEngine:
        from cc_sentiment.sentiment import AdapterFuser, SentimentClassifier

        try:
            await anyio.to_thread.run_sync(self._prefetch_total_bytes, model_repo)
            self.progress.state = "downloading"
            self._emit()
            fused_dir = await anyio.to_thread.run_sync(functools.partial(
                AdapterFuser.ensure_fused, model_repo, self.make_bytes_tqdm(self._on_byte_delta),
            ))
            self.progress.state = "loading"
            self._emit()
            classifier = SentimentClassifier(fused_dir)
            await classifier.ensure_loaded()
            self.classifier = classifier
            self.progress.state = "ready"
            self._emit()
            return classifier
        except Exception as exc:
            self.progress.state = "failed"
            self.progress.error = f"{type(exc).__name__}: {exc}"
            self._emit()
            raise

    def _prefetch_total_bytes(self, model_repo: str) -> None:
        from huggingface_hub import HfApi

        with contextlib.suppress(Exception):
            self.progress.bytes_total = sum(
                (s.size or 0) for s in HfApi().repo_info(model_repo, files_metadata=True).siblings
            )

    @staticmethod
    def make_bytes_tqdm(on_delta: Callable[[int], None]) -> type:
        class BytesTqdm(TqdmBase):
            def __init__(self, *args: Any, **kwargs: Any) -> None:
                kwargs.setdefault("file", io.StringIO())
                super().__init__(*args, **kwargs)

            def update(self, n: float | None = 1) -> bool | None:
                result = super().update(n)
                if n:
                    on_delta(int(n))
                return result

            def display(self, *args: Any, **kwargs: Any) -> None:
                return None

        return BytesTqdm
