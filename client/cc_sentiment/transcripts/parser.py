from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, Literal

from cc_transcript import (
    CLAUDE_PROJECTS_DIR,
    JUNK_USER_MESSAGE_RE,
    TranscriptDiscovery,
    apply_spec,
    parse_events,
)
from cc_transcript import TranscriptParser as CcTranscriptParser

from cc_sentiment.transcripts.adapter import ASSISTANT_TRUNCATION, to_messages
from cc_sentiment.transcripts.filterspec import SENTIMENT_SPEC
from cc_sentiment.transcripts.backend import ParsedTranscript
from cc_sentiment.transcripts.bucketer import (
    BUCKET_MINUTES,
    MIN_USER_TURNS_PER_SESSION,
    ConversationBucketer,
    extract_bucket_keys,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path

    from cc_sentiment.models import BucketKey

EPHEMERAL_ENTRYPOINTS: frozenset[str] = frozenset({"sdk-cli"})


class TranscriptParser:
    """Parses transcripts into cc-sentiment buckets via the shared cc-transcript
    backend, filtered by ``SENTIMENT_SPEC`` and adapted to ``TranscriptMessage``."""

    PREFETCH: ClassVar[int] = 8

    @classmethod
    def backend_name(cls) -> Literal["rust", "python"]:
        return CcTranscriptParser.backend_name()

    @classmethod
    def scan_bucket_keys(
        cls,
        directory: Path,
        *,
        name_contains: str | None = None,
        limit: int | None = None,
        known_mtimes: dict[str, float] | None = None,
    ) -> list[tuple[Path, float, list[BucketKey]]]:
        return [
            (path, mtime, extract_bucket_keys(list(to_messages(apply_spec(parse_events(path), SENTIMENT_SPEC)))))
            for path, mtime in TranscriptDiscovery.find_in(
                directory, name_contains=name_contains, limit=limit, known_mtimes=known_mtimes
            )
        ]

    @classmethod
    async def stream_transcripts(
        cls,
        paths: Sequence[tuple[Path, float]],
        *,
        prefetch: int | None = None,
    ) -> AsyncIterator[ParsedTranscript]:
        async for parsed in CcTranscriptParser.stream_transcripts(
            paths, prefetch=prefetch if prefetch is not None else cls.PREFETCH, spec=SENTIMENT_SPEC
        ):
            messages = to_messages(parsed.events)
            yield ParsedTranscript(
                path=parsed.path,
                mtime=parsed.mtime,
                bucket_keys=tuple(extract_bucket_keys(list(messages))),
                messages=messages,
            )
