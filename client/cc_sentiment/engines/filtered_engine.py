from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from functools import reduce

import anyio

from cc_sentiment.engines.protocol import NOOP_PROGRESS, InferenceEngine
from cc_sentiment.engines.score_filter import ScoreFilter
from cc_sentiment.models import ConversationBucket, SentimentScore


@dataclass(frozen=True)
class FilteredEngine:
    inner: InferenceEngine
    filters: tuple[ScoreFilter, ...]

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        async with anyio.create_task_group() as tg:
            for f in self.filters:
                tg.start_soon(f.prepare)

        prefilled: list[SentimentScore | None] = [
            next(
                (s for f in self.filters if (s := f.short_circuit(b)) is not None),
                None,
            )
            for b in buckets
        ]
        infer_idx = [i for i, p in enumerate(prefilled) if p is None]

        if pre := len(buckets) - len(infer_idx):
            on_progress(pre)

        inferred = (
            await self.inner.score([buckets[i] for i in infer_idx], on_progress)
            if infer_idx
            else []
        )
        filled = dict(zip(infer_idx, inferred))
        scored = [filled[i] if p is None else p for i, p in enumerate(prefilled)]

        return [
            reduce(lambda s, f: f.post_process(b, s), self.filters, score)
            for b, score in zip(buckets, scored)
        ]

    def peak_memory_gb(self) -> float:
        return self.inner.peak_memory_gb()

    async def close(self) -> None:
        await self.inner.close()
