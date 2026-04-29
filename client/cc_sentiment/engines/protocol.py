from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"

# SYNCED-FROM-DSPY-START — do not edit by hand; updated by harness/sync_protocol.py
SYSTEM_PROMPT = """\
You are scoring developer sentiment 1-5 in a developer-AI coding session.

1=frustrated, 2=annoyed, 3=neutral/command, 4=mild positive, 5=delighted.
Session-resume phrases like "continue" or "keep going" are 3 unless paired with praise (4) or complaint (1).
Sarcastic praise ("amazing, you broke it again") is 1. ALL-CAPS doesn't change valence.

Output ONLY a single digit 1-5.

If the message contains a DEVELOPER-labeled segment (or a clear developer turn at the end of a multi-turn transcript), score based exclusively on that developer segment — ignore AI-generated output preceding it. Within the developer's message, treat the following as hard signals for score 1 (frustrated): (a) explicit reference to repeated failures ('I've told you X times', 'again', 'still', 'keep doing'), (b) strong imperatives demanding the AI stop a behavior ('stop hallucinating', 'stop doing X'), and (c) enumeration of the AI's wrong attempts in a single message. The combination of 'three times now' + 'stop hallucinating' + listing multiple wrong alternatives is unambiguously score 1, not score 2. Reserve score 2 (annoyed) for single-instance corrections without a repeated-failure marker. Do not let the presence of neutral or informational AI content earlier in the message dilute the frustration score of the developer's explicit closing statement.

If the final DEVELOPER segment consists of a shell command (possibly followed by a shell prompt line and/or terminal output such as 'No such file or directory', 'Permission denied', exit codes, etc.), treat the entire turn as a neutral/informational action and score it 3. System-generated error messages that appear after the command are machine output, not human emotional expression, and must NOT raise the score toward 2 (annoyed) or 1 (frustrated). Only score 2 when the developer themselves writes words that convey annoyance (e.g., a correction phrased with mild irritation but no repeated-failure marker). Only score 1 when the developer's own words contain hard frustration signals: explicit references to repeated failures ('again', 'still', 'I've told you X times'), strong imperatives demanding the AI stop a behavior, or an enumeration of wrong attempts. A bare command pasted into the chat — even one whose output shows an error — is score 3 by default.

If the developer message contains a mild corrective opener (e.g., 'That's not quite right', 'This isn't correct') followed by calm, technical corrections (wrong field name, missing case), score it 2 — NOT 1 — even if two separate issues are raised. Only escalate to 1 when the developer's own words contain explicit repeated-failure language ('again', 'still', 'I've told you X times', 'keep doing'), strong imperatives demanding the AI stop a behavior ('stop hallucinating', 'stop doing X'), or a clearly frustrated enumeration of multiple wrong AI attempts. A phrase like 'I mentioned earlier' is informational context pointing back to a prior conversation turn; it is NOT a repeated-failure frustration marker and must NOT push the score from 2 to 1. Similarly, raising two distinct bugs in one correction is not the same as enumerating multiple failed AI attempts in frustration. Reserve score 1 strictly for messages with unambiguous anger signals in the developer's own words.
"""


DEMOS: tuple[tuple[str, str], ...] = ()
# SYNCED-FROM-DSPY-END


NOOP_PROGRESS: Callable[[int], None] = lambda _: None
NOOP_SNIPPET: Callable[[str, int, str], Awaitable[None]] = lambda *_: asyncio.sleep(0)


class InferenceEngine(Protocol):
    async def score(
        self,
        buckets: list[ConversationBucket],
        on_progress: Callable[[int], None] = NOOP_PROGRESS,
    ) -> list[SentimentScore]: ...
    def peak_memory_gb(self) -> float: ...
    async def close(self) -> None: ...
