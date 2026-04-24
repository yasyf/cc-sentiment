from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.text import build_bucket_messages, extract_score

from cc_sentiment.engines.protocol import NOOP_PROGRESS


class BaseEngine(ABC):
    @abstractmethod
    async def score_messages(
        self,
        message_lists: list[list[dict[str, str]]],
        on_progress: Callable[[int], None],
    ) -> list[str]: ...

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        responses = await self.score_messages(
            [build_bucket_messages(b) for b in buckets], on_progress
        )
        return [extract_score(r) for r in responses]

    def peak_memory_gb(self) -> float:
        return 0.0

    async def close(self) -> None:
        pass
