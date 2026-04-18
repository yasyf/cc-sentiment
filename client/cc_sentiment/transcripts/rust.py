from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from pathlib import Path
from typing import ClassVar, Literal

import anyio
import anyio.to_thread

from cc_sentiment import _transcripts_rs as rust
from cc_sentiment.models import BucketIndex, BucketKey, SessionId

from .backend import ParsedTranscript


class RustBackend:
    name: ClassVar[Literal["rust", "python"]] = "rust"
    RECV_BATCH: ClassVar[int] = 32

    async def parse_batch(
        self,
        paths: Sequence[tuple[Path, float]],
        *,
        prefetch: int,
    ) -> AsyncIterator[ParsedTranscript]:
        payload = [(str(p), m) for p, m in paths]
        stream = rust.stream_parse(payload, prefetch)
        while batch := await anyio.to_thread.run_sync(stream.recv_many, self.RECV_BATCH):
            for path, mtime, bucket_keys, messages in batch:
                yield ParsedTranscript(
                    path=Path(path),
                    mtime=mtime,
                    bucket_keys=bucket_keys,
                    messages=messages,
                )

    def scan_bucket_keys(
        self,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]:
        return [
            (
                Path(p),
                mtime,
                [BucketKey(SessionId(s), BucketIndex(i)) for s, i in keys],
            )
            for p, mtime, keys in rust.scan_bucket_keys(
                str(directory),
                name_contains=name_contains,
                limit=limit,
                known_mtimes=known_mtimes,
            )
        ]
