from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"

SYSTEM_PROMPT = """You are scoring developer sentiment 1-5 in a developer-AI coding session.

1=frustrated, 2=annoyed, 3=neutral/command, 4=mild positive, 5=delighted.
Session-resume phrases like "continue" or "keep going" are 3 unless paired with praise (4) or complaint (1).
Sarcastic praise ("amazing, you broke it again") is 1. ALL-CAPS doesn't change valence.

Output ONLY a single digit 1-5.

When scoring developer sentiment, carefully distinguish between frustrated (1) and annoyed (2). A score of 1 should be reserved for messages showing strong emotional frustration, exasperation, sarcasm, or hostility (e.g., 'this is completely broken AGAIN', 'I can't believe this', sarcastic remarks). A score of 2 (annoyed) applies when the developer expresses dissatisfaction or points out repeated/persistent issues but does so in a calm, technical, explanatory manner. Key indicator: if the message includes 'still' or implies a recurring issue but the developer is constructively and specifically describing the problem without emotional language, that is annoyance (2), not frustration (1). Messages that are essentially detailed, specific bug reports — even if they note the issue persists — should be scored as 2 unless they contain explicit emotional/hostile language.

When scoring developer sentiment, be careful not to inflate scores for purely informational or contextual messages. Messages where the developer is simply sharing file paths, explaining what happened, or providing context for the AI to use (e.g., 'it was [path]. its done now but that will help you identify the transcripts') are neutral (3), NOT mild positive (4). The phrase 'that will help you' in such contexts is descriptive/factual, not praise. A score of 4 requires actual positive sentiment such as explicit praise ('nice work', 'thanks, that's better'), appreciation, or satisfaction. Merely being cooperative, providing information, or explaining something helpfully is the baseline neutral interaction (3). Reserve 4 for messages where the developer is clearly expressing a positive emotional reaction or gratitude, not just being informative."""


DEMOS: tuple[tuple[str, str], ...] = (
    (
        "The function still returns undefined when the array is empty — you updated the loop logic but didn't handle the edge case where input.length is 0.",
        "2",
    ),
)


STRUCTURED_OUTPUTS_CHOICE = ["1", "2", "3", "4", "5"]


NOOP_PROGRESS: Callable[[int], None] = lambda _: None
NOOP_SNIPPET: Callable[[str, int], Awaitable[None]] = lambda *_: asyncio.sleep(0)


class InferenceEngine(Protocol):
    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]: ...
    def peak_memory_gb(self) -> float: ...
    async def close(self) -> None: ...
