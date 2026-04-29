from __future__ import annotations

import re
from typing import ClassVar

import anyio

from cc_sentiment.engines.filter import FrustrationFilter
from cc_sentiment.engines.score_filter import ScoreFilter
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


class ImperativeMildIrritationFilter(ScoreFilter):
    HOSTILE_LEXICON_FLOOR: ClassVar[int] = -3

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

    async def prepare(self) -> None:
        async with anyio.create_task_group() as tg:
            tg.start_soon(NLP.ensure_ready)
            tg.start_soon(Lexicon.ensure_ready)

    def post_process(
        self, bucket: ConversationBucket, score: SentimentScore
    ) -> SentimentScore:
        return (
            SentimentScore(2)
            if int(score) == 1 and self.should_demote(bucket)
            else score
        )
