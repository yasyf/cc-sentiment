from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path

import anyio
import anyio.to_thread

from cc_sentiment.engines import (
    NOOP_PROGRESS,
    NOOP_SNIPPET,
    InferenceEngine,
    build_engine,
)
from cc_sentiment.models import (
    BucketKey,
    BucketMetrics,
    ConversationBucket,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import (
    CLAUDE_PROJECTS_DIR,
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)

SNIPPET_FENCED_CODE = re.compile(r"```.*?```", re.DOTALL)
SNIPPET_INLINE_CODE = re.compile(r"`[^`]*`")
SNIPPET_LONG_PATH = re.compile(r"\S{40,}")
SNIPPET_WHITESPACE = re.compile(r"\s+")
SNIPPET_MAX_LEN = 80
STREAM_CHUNK_SIZE = 16


class Pipeline:
    @staticmethod
    def discover_new_transcripts(repo: Repository) -> list[tuple[Path, float]]:
        known = repo.file_mtimes()
        return [
            (path, mtime)
            for path in TranscriptDiscovery.find_transcripts()
            if (mtime := TranscriptDiscovery.transcript_mtime(path))
            and (str(path) not in known or known[str(path)] < mtime)
        ]

    @staticmethod
    async def count_new_buckets(repo: Repository, transcripts: list[tuple[Path, float]]) -> int:
        if not transcripts:
            return 0
        known = await anyio.to_thread.run_sync(repo.file_mtimes)
        scored_by_path = await anyio.to_thread.run_sync(repo.scored_buckets_for_all)

        def do_scan() -> int:
            scan = TranscriptParser.scan_bucket_keys(
                CLAUDE_PROJECTS_DIR, known_mtimes=known
            )
            return sum(
                1
                for path, _, keys in scan
                for key in keys
                if key not in scored_by_path.get(str(path), frozenset())
            )

        return await anyio.to_thread.run_sync(do_scan)

    @staticmethod
    def _parse_buckets_with_metrics(
        path: Path, scored_buckets: frozenset[BucketKey]
    ) -> tuple[list[ConversationBucket], dict[BucketKey, BucketMetrics]]:
        messages = TranscriptParser.parse_file(path)
        if not messages:
            return [], {}
        all_buckets = ConversationBucketer.bucket_messages(messages)

        by_session: dict[SessionId, list[ConversationBucket]] = {}
        for b in all_buckets:
            by_session.setdefault(b.session_id, []).append(b)

        metrics_by_key: dict[BucketKey, BucketMetrics] = {}
        for session_buckets in by_session.values():
            session_buckets.sort(key=lambda b: b.bucket_index)
            reads_so_far: set[str] = set()
            for bucket in session_buckets:
                metrics = BucketMetrics.from_messages_with_history(
                    bucket.messages, frozenset(reads_so_far)
                )
                metrics_by_key[
                    BucketKey(session_id=bucket.session_id, bucket_index=bucket.bucket_index)
                ] = metrics
                reads_so_far.update(
                    c.file_path
                    for m in bucket.messages
                    for c in m.tool_calls
                    if c.name == "Read" and c.file_path
                )

        new_buckets = [
            b for b in all_buckets
            if BucketKey(session_id=b.session_id, bucket_index=b.bucket_index)
            not in scored_buckets
        ]
        return new_buckets, metrics_by_key

    @staticmethod
    def snippet_for(bucket: ConversationBucket) -> str:
        for msg in bucket.messages:
            if msg.role != "user":
                continue
            stripped = SNIPPET_FENCED_CODE.sub(" ", msg.content)
            stripped = SNIPPET_INLINE_CODE.sub(" ", stripped)
            stripped = "\n".join(
                line for line in stripped.splitlines() if not line.lstrip().startswith(">")
            )
            stripped = SNIPPET_LONG_PATH.sub(" ", stripped)
            text = SNIPPET_WHITESPACE.sub(" ", stripped).strip()
            if not text:
                continue
            return text[:SNIPPET_MAX_LEN - 1] + "…" if len(text) > SNIPPET_MAX_LEN else text
        return ""

    @staticmethod
    def to_record(
        bucket: ConversationBucket, score: SentimentScore, metrics: BucketMetrics
    ) -> SentimentRecord:
        return SentimentRecord(
            time=bucket.bucket_start,
            conversation_id=bucket.session_id,
            bucket_index=bucket.bucket_index,
            sentiment_score=score,
            read_edit_ratio=metrics.read_edit_ratio,
            edits_without_prior_read_ratio=metrics.edits_without_prior_read_ratio,
            write_edit_ratio=metrics.write_edit_ratio,
            tool_calls_per_turn=metrics.tool_calls_per_turn,
            subagent_count=metrics.subagent_count,
            turn_count=metrics.turn_count,
            thinking_present=metrics.thinking_present,
            thinking_chars=metrics.thinking_chars,
            cc_version=metrics.cc_version,
            claude_model=metrics.claude_model,
        )

    @classmethod
    async def process_transcript(
        cls,
        path: Path,
        classifier: InferenceEngine,
        scored_buckets: frozenset[BucketKey] = frozenset(),
        on_bucket: Callable[[int], None] = NOOP_PROGRESS,
        on_snippet: Callable[[str, int], None] = NOOP_SNIPPET,
        on_records: Callable[[list[SentimentRecord]], None] = lambda _: None,
    ) -> list[SentimentRecord]:
        new_buckets, metrics_by_key = await anyio.to_thread.run_sync(
            cls._parse_buckets_with_metrics, path, scored_buckets
        )
        if not new_buckets:
            return []

        all_records: list[SentimentRecord] = []
        for start in range(0, len(new_buckets), STREAM_CHUNK_SIZE):
            chunk = new_buckets[start:start + STREAM_CHUNK_SIZE]
            scores = await classifier.score(chunk, on_progress=on_bucket)
            for bucket, score in zip(chunk, scores):
                if snippet := cls.snippet_for(bucket):
                    on_snippet(snippet, int(score))
            chunk_records = [
                cls.to_record(
                    bucket,
                    score,
                    metrics_by_key[BucketKey(
                        session_id=bucket.session_id, bucket_index=bucket.bucket_index
                    )],
                )
                for bucket, score in zip(chunk, scores)
            ]
            all_records.extend(chunk_records)
            on_records(chunk_records)

        return all_records

    @classmethod
    async def run(
        cls,
        repo: Repository,
        engine: str = "omlx",
        model_repo: str | None = None,
        new_transcripts: list[tuple[Path, float]] | None = None,
        on_records: Callable[[list[SentimentRecord]], None] = lambda _: None,
        on_bucket: Callable[[int], None] = NOOP_PROGRESS,
        on_engine_log: Callable[[str], None] | None = None,
        on_snippet: Callable[[str, int], None] = NOOP_SNIPPET,
        on_transcript_complete: Callable[[list[SentimentRecord]], None] = lambda _: None,
    ) -> list[SentimentRecord]:
        classifier = await build_engine(engine, model_repo, on_engine_log)

        try:
            transcripts = new_transcripts or await anyio.to_thread.run_sync(
                cls.discover_new_transcripts, repo
            )
            if not transcripts:
                return []

            all_records: list[SentimentRecord] = []

            for path, mtime in transcripts:
                scored_buckets = await anyio.to_thread.run_sync(
                    repo.scored_buckets_for, str(path)
                )
                records = await cls.process_transcript(
                    path, classifier, scored_buckets,
                    on_bucket=on_bucket, on_snippet=on_snippet,
                    on_records=on_records,
                )
                all_records.extend(records)
                await anyio.to_thread.run_sync(repo.save_records, str(path), mtime, records)
                if records:
                    on_transcript_complete(records)

            return all_records
        finally:
            await classifier.close()
