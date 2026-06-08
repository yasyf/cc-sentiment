from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cc_sentiment.models import BucketKey, TranscriptMessage


@dataclass(frozen=True)
class ParsedTranscript:
    path: Path
    mtime: float
    bucket_keys: tuple[BucketKey, ...]
    messages: tuple[TranscriptMessage, ...]
