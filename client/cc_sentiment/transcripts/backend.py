from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Literal, Protocol

from cc_sentiment.models import BucketKey, TranscriptMessage


@dataclass(frozen=True)
class ParsedTranscript:
    path: Path
    mtime: float
    bucket_keys: tuple[BucketKey, ...]
    messages: tuple[TranscriptMessage, ...]


class Backend(Protocol):
    name: ClassVar[Literal["rust", "python"]]

    def scan_bucket_keys(
        self,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]: ...

    def parse_batch(
        self,
        paths: Sequence[tuple[Path, float]],
        *,
        prefetch: int,
    ) -> AsyncIterator[ParsedTranscript]: ...
