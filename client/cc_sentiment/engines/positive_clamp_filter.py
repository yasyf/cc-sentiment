from __future__ import annotations

from collections.abc import Callable
from typing import ClassVar

from cc_sentiment.engines.protocol import NOOP_PROGRESS, InferenceEngine
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.nlp import NLP


class PositiveClampFilter:
    POSITIVE_LEXICON_FLOOR: ClassVar[int] = 3

    def __init__(self, inner: InferenceEngine) -> None:
        self.inner = inner

    @classmethod
    def has_positive_lexicon(cls, text: str) -> bool:
        if (nlp := NLP.get()) is None or Lexicon.afinn is None:
            return True
        return any(
            Lexicon.polarity(token.lemma_) >= cls.POSITIVE_LEXICON_FLOOR
            for token in nlp(text)
            if token.is_alpha
        )

    @classmethod
    def should_clamp_5(cls, bucket: ConversationBucket) -> bool:
        return not any(
            cls.has_positive_lexicon(msg.content)
            for msg in bucket.messages
            if msg.role == "user"
        )

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        await NLP.ensure_ready()
        await Lexicon.ensure_ready()
        scores = await self.inner.score(buckets, on_progress)
        return [
            SentimentScore(3) if int(score) == 5 and self.should_clamp_5(bucket) else score
            for bucket, score in zip(buckets, scores)
        ]

    def peak_memory_gb(self) -> float:
        return self.inner.peak_memory_gb()

    async def close(self) -> None:
        await self.inner.close()
