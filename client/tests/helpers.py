from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from cc_transcript import parse_event
from cc_transcript.models import AssistantEvent, UserEvent
from cc_transcript.sentiment.buckets import ConversationEvent

from cc_sentiment.models import (
    BucketIndex,
    BucketKey,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.pipeline import ScannedTranscript, ScanResult
from cc_sentiment.transcripts import ConversationBucketer, ParsedTranscript

DEFAULT_TIMESTAMP = datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc)


def make_user_event(
    text: str,
    *,
    uuid: str = "u1",
    session_id: str = "s",
    timestamp: datetime | None = None,
    cc_version: str | None = "2.1.92",
) -> UserEvent:
    event = parse_event(
        {
            "type": "user",
            "uuid": uuid,
            "parentUuid": None,
            "sessionId": session_id,
            "timestamp": (timestamp or DEFAULT_TIMESTAMP).isoformat(),
            "message": {"role": "user", "content": text},
        }
        | ({"version": cc_version} if cc_version else {})
    )
    assert isinstance(event, UserEvent)
    return event


def make_assistant_event(
    text: str,
    *,
    uuid: str = "a1",
    session_id: str = "s",
    timestamp: datetime | None = None,
    model: str = "claude-sonnet-4-20250514",
    blocks: Sequence[Mapping[str, Any]] = (),
) -> AssistantEvent:
    event = parse_event(
        {
            "type": "assistant",
            "uuid": uuid,
            "parentUuid": None,
            "sessionId": session_id,
            "timestamp": (timestamp or DEFAULT_TIMESTAMP).isoformat(),
            "message": {
                "role": "assistant",
                "model": model,
                "content": [{"type": "text", "text": text}, *blocks],
            },
        }
    )
    assert isinstance(event, AssistantEvent)
    return event


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
    events: Sequence[ConversationEvent],
    mtime: float = 0.0,
) -> ParsedTranscript:
    buckets = ConversationBucketer.bucket_events(events)
    return ParsedTranscript(
        path=path,
        mtime=mtime,
        bucket_keys=tuple(
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            for b in buckets
        ),
        events=tuple(events),
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
