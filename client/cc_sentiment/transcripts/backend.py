from __future__ import annotations

from pathlib import Path
from typing import ClassVar, Literal, Protocol

from cc_sentiment.models import BucketKey, TranscriptMessage


class Backend(Protocol):
    name: ClassVar[Literal["rust", "python"]]

    def parse_line(self, line: str) -> TranscriptMessage | None: ...

    def parse_file(self, path: Path) -> list[TranscriptMessage]: ...

    def bucket_keys_for(self, path: Path) -> list[BucketKey]: ...

    def scan_bucket_keys(
        self,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]: ...
