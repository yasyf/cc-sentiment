from __future__ import annotations

import re

from cc_sentiment.models import ConversationBucket, SentimentScore

MAX_CONVERSATION_CHARS = 8192


def format_conversation(bucket: ConversationBucket) -> str:
    full = "\n".join(
        f"{'DEVELOPER' if msg.role == 'user' else 'AI'}: {msg.content}"
        for msg in bucket.messages
    )
    if len(full) > MAX_CONVERSATION_CHARS:
        return full[:MAX_CONVERSATION_CHARS] + "\n[... truncated]"
    return full


def extract_score(response: str) -> SentimentScore:
    cleaned = response.replace("<pad>", "").strip()
    if cleaned in "12345":
        return SentimentScore(int(cleaned))
    if match := re.search(r"[1-5]", cleaned):
        return SentimentScore(int(match.group()))
    raise ValueError(f"Could not extract score from: {cleaned!r}")
