from __future__ import annotations

from datetime import datetime, timezone

from cc_sentiment.models import (
    BucketIndex,
    SentimentRecord,
    SentimentScore,
    SessionId,
)


def make_record(
    session_id: str = "session-1",
    bucket_index: int = 0,
    score: int = 4,
    claude_model: str = "claude-sonnet-4-20250514",
) -> SentimentRecord:
    return SentimentRecord(
        time=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
        conversation_id=SessionId(session_id),
        bucket_index=BucketIndex(bucket_index),
        sentiment_score=SentimentScore(score),
        claude_model=claude_model,
        read_edit_ratio=None,
        edits_without_prior_read_ratio=None,
        write_edit_ratio=None,
        tool_calls_per_turn=0.0,
        subagent_count=0,
        turn_count=1,
        thinking_present=False,
        thinking_chars=0,
        cc_version="2.1.92",
    )
