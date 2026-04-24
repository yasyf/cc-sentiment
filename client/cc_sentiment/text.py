from __future__ import annotations

import re

from cc_sentiment.models import ConversationBucket, SentimentScore

MAX_CONVERSATION_CHARS = 8192
SCORE_RX = re.compile(r"[1-5]")


def format_conversation(bucket: ConversationBucket) -> str:
    full = "\n".join(
        f"{'DEVELOPER' if msg.role == 'user' else 'AI'}: {msg.content}"
        for msg in bucket.messages
    )
    return (
        full[:MAX_CONVERSATION_CHARS] + "\n[... truncated]"
        if len(full) > MAX_CONVERSATION_CHARS
        else full
    )


def extract_score(response: str) -> SentimentScore:
    cleaned = response.replace("<pad>", "").strip()
    if cleaned in "12345":
        return SentimentScore(int(cleaned))
    if match := SCORE_RX.search(cleaned):
        return SentimentScore(int(match.group()))
    raise ValueError(f"Could not extract score from: {cleaned!r}")


def build_user_content(text: str) -> str:
    return f"CONVERSATION:\nDEVELOPER: {text.strip()}"


def build_bucket_user_content(bucket: ConversationBucket) -> str:
    return f"CONVERSATION:\n{format_conversation(bucket)}"


def build_prefix_messages() -> list[dict[str, str]]:
    from cc_sentiment.engines.protocol import DEMOS, SYSTEM_PROMPT

    prefix: list[dict[str, str]] = [{"role": "system", "content": SYSTEM_PROMPT}]
    for demo_msg, demo_score in DEMOS:
        prefix.append({"role": "user", "content": build_user_content(demo_msg)})
        prefix.append({"role": "assistant", "content": demo_score})
    return prefix


def build_bucket_messages(bucket: ConversationBucket) -> list[dict[str, str]]:
    return [
        *build_prefix_messages(),
        {"role": "user", "content": build_bucket_user_content(bucket)},
    ]
