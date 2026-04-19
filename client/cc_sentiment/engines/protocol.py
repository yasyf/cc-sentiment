from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"

SYSTEM_PROMPT = """Rate the developer's sentiment in this developer-AI conversation. Reply with ONLY a single digit 1-5.

1 - Frustrated, angry, or giving up. Examples: "this still doesn't work", "ugh why did you do that", "forget it, i'll do it myself", "fucking hell".
2 - Annoyed, pointing out mistakes. Examples: "that's wrong, try again", "you missed the null case", "no, the other file".
3 - Neutral, just giving instructions, urgent/commanding direction, or routine approvals. Examples: "go ahead and commit", "add a test for this", "run the linter", "sounds good, proceed", "yes", "ok", "keep going no matter how long", "don't stop, force remove it", "just do it".
4 - Satisfied, says it works well. Examples: "perfect", "that works", "nice, looks good".
5 - Enthusiastic praise, amazement, strong positive emotion. Examples: "incredible!", "this is amazing", "blown away", "love it!!!".

CRITICAL RULE — exclamations indicate intensity, not valence:
- Map to 5 ONLY for unambiguously POSITIVE words: love, incredible, amazing, blown away, phenomenal, fantastic, brilliant, beautiful, nailed it.
- Map to 1 for NEGATIVE exclamations: holy shit, fucking hell, oh my god, wtf, ugh, goddammit, jesus christ, damn it.
- "HOLY SHIT THIS IS BROKEN!!!" is 1. "holy shit this is amazing" is 5. The exclamation is rage or delight based on the words, not the punctuation.

Other rules:
- Routine greenlights like "go ahead", "yes do it", "sounds good", "ok commit" are 3 (neutral), not 1. Simple approval without strong emotion ("works", "good", "nice") is 4.
- Urgency or insistence ("don't stop", "keep going no matter how long", "force X", "just do it") is 3 unless the developer also expresses anger. Commanding tone is not frustration.

Score ONLY the developer's messages."""


STRUCTURED_OUTPUTS_CHOICE = ["1", "2", "3", "4", "5"]


NOOP_PROGRESS: Callable[[int], None] = lambda _: None


async def noop_snippet(_s: str, _i: int) -> None:
    return None


NOOP_SNIPPET: Callable[[str, int], Awaitable[None]] = noop_snippet


class InferenceEngine(Protocol):
    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]: ...
    def peak_memory_gb(self) -> float: ...
    async def close(self) -> None: ...
