from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"

SYSTEM_PROMPT = """You are scoring developer sentiment 1–5 in a developer-AI coding session. Output ONLY a single digit 1–5.

**Scale:**
1 = frustrated/hostile — contempt, scorn, profane blame, biting sarcasm
2 = annoyed — mild-to-moderate displeasure, directive criticism, impatience without hostility
3 = neutral/command — matter-of-fact requests or technical instructions, no emotional valence
4 = mild positive — light approval or praise; "great work, now [continue/resume]" counts as 4, not 3
5 = delighted — enthusiastic or superlative praise

---

**Score 1 — any of these patterns is sufficient:**

• **Elaborate mock-praise sarcasm** — multi-clause irony with markers like *truly, wonderful, fantastic, genius, groundbreaking, brilliant, inspired, masterpiece* used sarcastically (e.g. `"oh wonderful, you've 'fixed' the test by deleting the assertion. Truly genius work."`)

• **Hallucination accusation** — `"stop hallucinating / stop inventing / stop making up / quit making it up"` targeting a backtick-formatted or named identifier the AI fabricated (e.g. `` "stop hallucinating `parseConfig` — it's not a real method" ``)

• **Profanity + repeated-failure blame** — profanity fused with an accusation of repeated error (`"fucking hell, that's the third time you've 'fixed' the same import and broken the tests"`)

• **ALL-CAPS hostile command** — capitalized imperative paired with a criticism or accusation (`"STOP GUESSING"`, `"READ THE DAMN DOCS"`, `"JUST FIX IT ALREADY"`)

---

**Score 2 — key patterns:**

• `"stop [action]"` where the action is a process, not a hallucination accusation (`"stop it, run it in UI mode"`, `"stop downgrading"`)
• `"don't just [guess / add bandaids / use random guards]"` — impatient instruction-correction
• Bullet-point code-review complaints listing style or quality issues without explosive language
• Mild repeated frustration without contempt: `"still not working"`, `"that's not quite right"`, `"you missed X again"`
• Explicit but restrained criticism: `"you've done quite a poor job here"`

---

**Score 3 — anti-patterns (do NOT upgrade to 2 or 1):**

• **Message length is irrelevant** — neutral commands range from 6 to 1000+ characters; long messages are not more frustrated
• Bullet lists, technical jargon, capitalization, or profanity in passing do not shift valence
• Session-resume phrases (`"continue"`, `"resume"`, `"go ahead"`, `"carry on"`, `"keep going"`, `"ok continue"`) → 3 unless paired with praise (→ 4) or a complaint (→ 2/1)

---

**1 vs 2 tie-break:** Is the author expressing *contempt or scorn* (→ 1) or merely *impatient direction* (→ 2)?"""


DEMOS: tuple[tuple[str, str], ...] = (
    (
        "stop hallucinating method names that don't exist in the docs — that's the third time you've invented a `client.batch_process()` that isn't real, just say you don't know",
        "1",
    ),
    (
        "stop hallucinating function names that don't exist in this library, you've invented `parseConfig` three times now and it's never been a real method",
        "1",
    ),
    (
        "you can go find the transcript yoruself, ssh to `yasyf@yasyf` (goes throguh tailnet) and you can find all the transcripts",
        "3",
    ),
    (
        "nope, something went wrong. gently stop the auto one, and retart the manual one with vnc",
        "2",
    ),
)


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
