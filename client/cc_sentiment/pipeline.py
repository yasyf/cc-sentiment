from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import anyio
import anyio.to_thread

from cc_sentiment.engines import HAIKU_MODEL, ClaudeCLIEngine, InferenceEngine, OMLXEngine
from cc_sentiment.models import (
    BucketKey,
    BucketMetrics,
    ConversationBucket,
    SentimentRecord,
    SessionId,
)
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import (
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)


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
    def count_new_buckets(repo: Repository, transcripts: list[tuple[Path, float]]) -> int:
        return sum(
            sum(
                BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) not in scored
                for b in ConversationBucketer.bucket_messages(messages)
            )
            for path, _ in transcripts
            if (messages := TranscriptParser.parse_file(path))
            if (scored := repo.scored_buckets_for(str(path))) is not None
        )

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

    @classmethod
    async def process_transcript(
        cls,
        path: Path,
        classifier: InferenceEngine,
        scored_buckets: frozenset[BucketKey] = frozenset(),
        on_bucket: Callable[[int], None] | None = None,
    ) -> list[SentimentRecord]:
        new_buckets, metrics_by_key = await anyio.to_thread.run_sync(
            cls._parse_buckets_with_metrics, path, scored_buckets
        )
        if not new_buckets:
            return []

        scores = await classifier.score(new_buckets, on_progress=on_bucket)

        return [
            SentimentRecord(
                time=bucket.bucket_start,
                conversation_id=bucket.session_id,
                bucket_index=bucket.bucket_index,
                sentiment_score=score,
                read_edit_ratio=(
                    m := metrics_by_key[
                        BucketKey(session_id=bucket.session_id, bucket_index=bucket.bucket_index)
                    ]
                ).read_edit_ratio,
                edits_without_prior_read_ratio=m.edits_without_prior_read_ratio,
                write_edit_ratio=m.write_edit_ratio,
                tool_calls_per_turn=m.tool_calls_per_turn,
                subagent_count=m.subagent_count,
                turn_count=m.turn_count,
                thinking_present=m.thinking_present,
                thinking_chars=m.thinking_chars,
                cc_version=m.cc_version,
                claude_model=m.claude_model,
            )
            for bucket, score in zip(new_buckets, scores)
        ]

    @classmethod
    async def run(
        cls,
        repo: Repository,
        engine: str = "omlx",
        model_repo: str | None = None,
        new_transcripts: list[tuple[Path, float]] | None = None,
        on_records: Callable[[list[SentimentRecord]], None] | None = None,
        on_bucket: Callable[[int], None] | None = None,
    ) -> list[SentimentRecord]:
        match engine:
            case "mlx":
                from cc_sentiment.sentiment import SentimentClassifier
                classifier: InferenceEngine = (
                    SentimentClassifier(model_repo) if model_repo
                    else SentimentClassifier()
                )
            case "omlx":
                classifier = await anyio.to_thread.run_sync(OMLXEngine, model_repo)
                await classifier.warm_system_prompt()
            case "claude":
                classifier = ClaudeCLIEngine(model=model_repo or HAIKU_MODEL)
            case _:
                raise ValueError(f"Unknown engine: {engine}")

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
                    path, classifier, scored_buckets, on_bucket=on_bucket
                )
                all_records.extend(records)
                await anyio.to_thread.run_sync(repo.save_records, str(path), mtime, records)

                if on_records and records:
                    on_records(records)

            return all_records
        finally:
            await classifier.close()
