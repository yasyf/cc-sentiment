from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar, Literal

import anyio
import anyio.to_thread

from cc_sentiment import _transcripts_rs as rust
from cc_sentiment.models import (
    AssistantMessage,
    BucketIndex,
    BucketKey,
    SessionId,
    ToolCall,
    TranscriptMessage,
    UserMessage,
)

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
        while True:
            batch = await anyio.to_thread.run_sync(stream.recv_many, self.RECV_BATCH)
            if not batch:
                return
            for item in batch:
                yield self.parsed_from_tuple(item)

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

    @classmethod
    def parsed_from_tuple(cls, data: tuple[Any, ...]) -> ParsedTranscript:
        path, mtime, bucket_keys, messages = data
        return ParsedTranscript(
            path=Path(path),
            mtime=mtime,
            bucket_keys=tuple(
                BucketKey(SessionId(s), BucketIndex(i)) for s, i in bucket_keys
            ),
            messages=tuple(cls.message_from_tuple(m) for m in messages),
        )

    @staticmethod
    def message_from_tuple(data: tuple[Any, ...]) -> TranscriptMessage:
        kind = data[0]
        if kind == "u":
            _, content, ts_str, session_id_str, uuid, cc_version = data
            return UserMessage(
                content,
                datetime.fromisoformat(ts_str),
                SessionId(session_id_str),
                uuid,
                (),
                0,
                cc_version,
            )
        if kind == "a":
            (
                _,
                content,
                ts_str,
                session_id_str,
                uuid,
                claude_model,
                thinking_chars,
                tool_calls,
            ) = data
            return AssistantMessage(
                content,
                datetime.fromisoformat(ts_str),
                SessionId(session_id_str),
                uuid,
                tuple(ToolCall(n, fp) for n, fp in tool_calls),
                thinking_chars,
                claude_model,
            )
        raise ValueError(f"unknown message kind: {kind!r}")
