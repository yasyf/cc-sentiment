from __future__ import annotations

from typing import ClassVar

import anyio

from cc_sentiment.engines.score_filter import ScoreFilter
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.models import ConversationBucket, SentimentScore
from cc_sentiment.nlp import NLP


class PositiveClampFilter(ScoreFilter):
    POSITIVE_LEXICON_FLOOR: ClassVar[int] = 3
    MAX_WORDS_FOR_CLAMP: ClassVar[int] = 3

    @classmethod
    def is_short(cls, text: str) -> bool:
        return len(text.split()) <= cls.MAX_WORDS_FOR_CLAMP

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
        return any(
            msg.role == "user"
            and cls.is_short(msg.content)
            and not cls.has_positive_lexicon(msg.content)
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
            SentimentScore(3)
            if int(score) == 5 and self.should_clamp_5(bucket)
            else score
        )
