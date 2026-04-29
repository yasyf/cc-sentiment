from __future__ import annotations

from abc import ABC

from cc_sentiment.models import ConversationBucket, SentimentScore


class ScoreFilter(ABC):
    async def prepare(self) -> None:
        return None

    def short_circuit(self, bucket: ConversationBucket) -> SentimentScore | None:
        return None

    def post_process(
        self, bucket: ConversationBucket, score: SentimentScore
    ) -> SentimentScore:
        return score
