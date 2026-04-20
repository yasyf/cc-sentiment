from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from cc_sentiment.models import ConversationBucket, SentimentScore

DEFAULT_MODEL = "unsloth/gemma-4-E2B-it-UD-MLX-4bit"

SYSTEM_PROMPT = """You are scoring developer sentiment in a developer-AI coding conversation. Reply with ONLY a single digit 1-5.

# SCALE

1 = frustrated / angry / giving up. Includes sarcastic praise ("great, you ignored...", "amazing how you break things").
2 = annoyed, actively correcting the AI ("no, that's wrong", "you missed X", "still not working").
3 = neutral / commanding / matter-of-fact, including blunt orders even with mild swearing ("delete that file", "SHIP IT", "stop the server").
4 = mild positive / satisfied. The developer acknowledges something works. Low-key praise counts.
5 = strong positive / delighted / amazed. Elation, hype, enthusiasm.

# MILD-POSITIVE LEXICON — these are ALWAYS 4, not 3

"nice", "cool", "good", "sweet", "works", "perfect", "great", "ok cool",
"LGTM", "lgtm", "ship it looks right", "can't complain", "not bad", "good enough",
"thx", "thanks", "sounds good", "works for me", "looks good", "ship it" (when responding to a completed task).

Praise at the START of a longer instruction still counts: "great, now add X" = 4.

# STRONG-POSITIVE LEXICON — these are 5

"YOOOO", "holy shit this is beautiful", "incredible", "phenomenal",
"amazing" (literal, not sarcastic), "love it", "you nailed it", "god tier",
"🔥", "banger", "legendary", "perfect!!" (multi-exclamation).

# FRUSTRATION LEXICON — these are 1

- Imperatives to correct AI misbehavior: "stop guessing", "stop hallucinating",
  "stop making shit up", "stop being lazy", "stop pretending". ALL-CAPS amplifies but doesn't change.
- Swears of frustration: "fucking hell", "wtf", "ugh", "holy shit this is broken",
  "wait what the fuck", "jesus christ".
- Sarcastic praise: "great, you completely ignored X", "amazing how you keep breaking things",
  "wow, genius move", "nice job breaking it". The sarcasm marker is praise + criticism of AI behavior.

# AMBIGUITY GUARDS

- "stop X" is 1 only if X is an AI misbehavior. If X is a server/file/process, it's 3.
- ALL-CAPS doesn't change valence: "SHIP IT" is 3, "STOP GUESSING" is 1.
- "holy shit this broke" is 1; "holy shit this is beautiful" is 5.
- Sarcasm detector: if the sentence starts with praise but then describes the AI doing something harmful or stupid ("great, you ignored the spec", "amazing how you keep finding new ways to break things"), it is 1.
- A blunt command with profanity aimed at legacy code (not the AI) is 3: "delete that whole damn file" = 3.

Score ONLY the developer's messages. Output a single digit 1-5."""


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
