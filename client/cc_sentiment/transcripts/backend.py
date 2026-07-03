from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_transcript.sentiment.buckets import ConversationEvent

from cc_sentiment.models import BucketKey


@dataclass(frozen=True)
class ParsedTranscript:
    path: Path
    mtime: float
    bucket_keys: tuple[BucketKey, ...]
    events: tuple[ConversationEvent, ...]
