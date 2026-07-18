from __future__ import annotations

import functools
from typing import TYPE_CHECKING, ClassVar

import anyio.to_thread
from cc_transcript import CLAUDE_PROJECTS_DIR, JUNK_USER_MESSAGE_RE, find_in, stream
from cc_transcript.models import AssistantEvent, UserEvent

from cc_sentiment.transcripts.backend import ParsedTranscript
from cc_sentiment.transcripts.bucketer import (
    BUCKET_MINUTES,
    MIN_USER_TURNS_PER_SESSION,
    ConversationBucketer,
    extract_bucket_keys,
)
from cc_sentiment.transcripts.filterspec import SENTIMENT_SPEC

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from cc_sentiment.models import BucketKey


class TranscriptParser:
    """Parses transcripts into cc-sentiment buckets via the shared cc-transcript
    engine, filtered by ``SENTIMENT_SPEC`` down to the conversational event spine."""

    PREFETCH: ClassVar[int] = 8

    @classmethod
    async def scan_bucket_keys(
        cls,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]:
        pairs = await anyio.to_thread.run_sync(
            functools.partial(
                find_in, directory, name_contains=name_contains, limit=limit, known_mtimes=known_mtimes
            )
        )
        return sorted(
            [
                (parsed.path, parsed.mtime, list(parsed.bucket_keys))
                async for parsed in cls.stream_transcripts([path for path, _ in pairs])
            ],
            key=lambda t: t[0],
        )

    @classmethod
    async def stream_transcripts(
        cls,
        paths: Sequence[Path],
        *,
        prefetch: int | None = None,
    ) -> AsyncIterator[ParsedTranscript]:
        transcripts = stream(
            paths, drop=SENTIMENT_SPEC, prefetch=prefetch if prefetch is not None else cls.PREFETCH
        )
        while (parsed := await anyio.to_thread.run_sync(next, transcripts, None)) is not None:
            events = tuple(e for e in parsed.events if isinstance(e, UserEvent | AssistantEvent))
            yield ParsedTranscript(
                path=parsed.path,
                mtime=parsed.mtime,
                bucket_keys=tuple(extract_bucket_keys(events)),
                events=events,
            )
