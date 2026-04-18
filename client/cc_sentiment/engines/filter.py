from __future__ import annotations

import re
from collections.abc import Callable

from cc_sentiment.models import ConversationBucket, SentimentScore

from cc_sentiment.engines.protocol import NOOP_PROGRESS, InferenceEngine

FRUSTRATION_PATTERN = re.compile(
    r"\b("
    r"wtf|wth|ffs|omfg|"
    r"shit(?:ty|tiest)?|dumbass|horrible|awful|"
    r"piss(?:ed|ing)?off|piece\s*of\s*(?:shit|crap|junk)|"
    r"what\s*the\s*(?:fuck|hell)|"
    r"fuck(?:ing?)?\s*(?:broken|useless|terrible|awful|horrible)|"
    r"fuck\s*you|screw\s*(?:this|you)|"
    r"so\s*frustrating|this\s*sucks|damnit|damn\s*it|"
    r"no,?\s*that'?s\s*wrong|not\s*what\s*i\s*asked|"
    r"you\s*misunderstood|that'?s\s*not\s*right|"
    r"undo\s*that|why\s*did\s*you|try\s*again|"
    r"useless|this\s*is\s*terrible|completely\s*wrong|"
    r"i\s*give\s*up|giving\s*up"
    r")\b",
    re.IGNORECASE,
)


class FrustrationFilter:
    def __init__(self, inner: InferenceEngine) -> None:
        self.inner = inner

    @staticmethod
    def check_frustration(bucket: ConversationBucket) -> bool:
        return any(
            msg.role == "user" and FRUSTRATION_PATTERN.search(msg.content)
            for msg in bucket.messages
        )

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
