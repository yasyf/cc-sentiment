from __future__ import annotations

import re
from collections.abc import Callable
from typing import ClassVar

from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.protocol import NOOP_PROGRESS, InferenceEngine
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.nlp import NLP

MILD_IMPATIENCE_PATTERN = re.compile(
    r"\b("
    r"and\s+again|"
    r"yet\s+again|"
    r"once\s+again|"
    r"for\s+the\s+(?:second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|umpteenth|hundredth|nth)\s+time"
    r")\b",
    re.IGNORECASE,
)


class ImperativeMildIrritationFilter:
    HOSTILE_LEXICON_FLOOR: ClassVar[int] = -3

    def __init__(self, inner: InferenceEngine) -> None:
        self.inner = inner

    @staticmethod
    def matches_trigger(text: str) -> bool:
        return MILD_IMPATIENCE_PATTERN.search(text) is not None

    @classmethod
    def has_hostile_lexicon(cls, text: str) -> bool:
        if FrustrationFilter.matches_text(text):
            return True
        if (nlp := NLP.get()) is None or Lexicon.afinn is None:
            return True
        return any(
            Lexicon.polarity(token.lemma_) <= cls.HOSTILE_LEXICON_FLOOR
            for token in nlp(text)
            if token.is_alpha
        )

    @classmethod
    def should_demote(cls, bucket: ConversationBucket) -> bool:
        return any(
            msg.role == "user"
            and cls.matches_trigger(msg.content)
            and not cls.has_hostile_lexicon(msg.content)
            for msg in bucket.messages
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
            SentimentScore(2) if int(score) == 1 and self.should_demote(bucket) else score
            for bucket, score in zip(buckets, scores)
        ]

    def peak_memory_gb(self) -> float:
        return self.inner.peak_memory_gb()

    async def close(self) -> None:
        await self.inner.close()
