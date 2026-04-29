from __future__ import annotations

from typing import ClassVar

from cc_sentiment.engines.score_filter import ScoreFilter
from cc_sentiment.models import ConversationBucket, SentimentScore

TRAILING_PUNCT = ".!?…,;:"


class SessionResumeFilter(ScoreFilter):
    RESUME_PHRASES: ClassVar[frozenset[str]] = frozenset(
        {
            "continue",
            "continues",
            "continue please",
            "please continue",
            "resume",
            "resume please",
            "please resume",
            "go ahead",
            "keep going",
            "carry on",
            "proceed",
            "go on",
            "keep at it",
            "ok continue",
            "ok resume",
            "ok go ahead",
            "okay continue",
            "okay resume",
            "okay go ahead",
            "continue from where you left off",
            "continue where you left off",
            "continue your previous task",
            "[context restored] resume",
            "[context restored] continue",
            "[context restored] pick up the conversation",
        }
    )

    @classmethod
    def is_bare_resume(cls, text: str) -> bool:
        return text.strip().rstrip(TRAILING_PUNCT).strip().lower() in cls.RESUME_PHRASES

    @classmethod
    def should_clamp(cls, bucket: ConversationBucket) -> bool:
        return any(
            msg.role == "user" and cls.is_bare_resume(msg.content)
            for msg in bucket.messages
        )

    def post_process(
        self, bucket: ConversationBucket, score: SentimentScore
    ) -> SentimentScore:
        return SentimentScore(3) if self.should_clamp(bucket) else score
