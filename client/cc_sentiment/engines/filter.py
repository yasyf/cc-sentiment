from __future__ import annotations

import re
from collections.abc import Callable

from cc_sentiment.models import ConversationBucket, SentimentScore

from cc_sentiment.engines.protocol import NOOP_PROGRESS, InferenceEngine

FRUSTRATION_PATTERN = re.compile(
    r"\b("
    r"wtf|wth|ffs|omfg|"
    r"fuck(?:ing)?\s*(?:broken|useless|stupid|terrible|awful|horrible|piece\s*of\s*shit)|"
    r"fuck\s*(?:you|this|it|off)|"
    r"screw\s*(?:you|this)|"
    r"piece\s*of\s*(?:shit|crap|junk)|"
    r"dumbass|"
    r"you'?re\s*(?:fucking\s*)?(?:useless|stupid|broken|terrible|awful)|"
    r"this\s*(?:is\s*)?(?:fucking\s*)?(?:terrible|awful|horrible|useless|garbage)|"
    r"stop\s+(?:guessing|making\s+(?:things|stuff|shit)\s*up|hallucinating|inventing|assuming|pretending|being\s+lazy|making\s+excuses|lying)|"
    r"just\s+stop\s+(?:it|already)|"
    r"completely\s*useless|"
    r"i\s*give\s*up|giving\s*up"
    r")\b",
    re.IGNORECASE,
)


class FrustrationFilter:
    def __init__(self, inner: InferenceEngine) -> None:
        self.inner = inner

    @staticmethod
    def matches_text(text: str) -> bool:
        return FRUSTRATION_PATTERN.search(text) is not None

    @classmethod
    def matched_user_message(cls, bucket: ConversationBucket) -> str | None:
        return next(
            (msg.content for msg in bucket.messages if msg.role == "user" and cls.matches_text(msg.content)),
            None,
        )

    @staticmethod
    def matched_words(bucket: ConversationBucket) -> list[str]:
        return [
            match.group(1).lower().strip()
            for msg in bucket.messages
            if msg.role == "user"
            for match in FRUSTRATION_PATTERN.finditer(msg.content)
        ]

    @classmethod
    def check_frustration(cls, bucket: ConversationBucket) -> bool:
        return cls.matched_user_message(bucket) is not None

    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]:
        flags = [self.check_frustration(b) for b in buckets]
        to_infer = [(i, b) for i, (b, f) in enumerate(zip(buckets, flags)) if not f]
        if pre := sum(flags):
            on_progress(pre)
        inferred = await self.inner.score([b for _, b in to_infer], on_progress)
        scores = [SentimentScore(1) if f else SentimentScore(0) for f in flags]
        for (idx, _), s in zip(to_infer, inferred):
            scores[idx] = s
        return scores

    def peak_memory_gb(self) -> float:
        return self.inner.peak_memory_gb()

    async def close(self) -> None:
        await self.inner.close()
