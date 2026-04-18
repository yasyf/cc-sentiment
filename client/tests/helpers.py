from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    SentimentRecord,
    SentimentScore,
    SessionId,
    TranscriptMessage,
)
from cc_sentiment.pipeline import ScannedTranscript, ScanResult
from cc_sentiment.transcripts import ConversationBucketer, ParsedTranscript


def make_record(
    session_id: str = "session-1",
    bucket_index: int = 0,
    score: int = 4,
    claude_model: str = "claude-sonnet-4-20250514",
    time: datetime | None = None,
) -> SentimentRecord:
    return SentimentRecord(
        time=time or datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
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


def make_parsed(
    path: Path,
    messages: Sequence[TranscriptMessage],
    mtime: float = 0.0,
) -> ParsedTranscript:
    buckets = ConversationBucketer.bucket_messages(list(messages))
    return ParsedTranscript(
        path=path,
        mtime=mtime,
        bucket_keys=tuple(
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            for b in buckets
        ),
        messages=tuple(messages),
    )


def make_scan(path: Path | None = None, buckets: int = 0) -> ScanResult:
    if path is None:
        return ScanResult(transcripts=(), scored_by_path={})
    return ScanResult(
        transcripts=(
            ScannedTranscript(
                path=path,
                mtime=0.0,
                new_bucket_keys=tuple(
                    BucketKey(
                        session_id=SessionId(f"s{i}"),
                        bucket_index=BucketIndex(i),
                    )
                    for i in range(buckets)
                ),
            ),
        ),
        scored_by_path={},
    )
