from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from cc_sentiment.engines import InferenceEngine, OMLXEngine
from cc_sentiment.models import (
    AppState,
    BucketKey,
    ProcessedFile,
    ProcessedSession,
    SentimentRecord,
    SessionId,
)
from cc_sentiment.transcripts import (
    ConversationBucketer,
    TranscriptDiscovery,
    TranscriptParser,
)


class Pipeline:
    @staticmethod
    def discover_new_transcripts(state: AppState) -> list[tuple[Path, float]]:
        return [
            (path, mtime)
            for path in TranscriptDiscovery.find_transcripts()
            if (mtime := TranscriptDiscovery.transcript_mtime(path))
            and (
                (key := str(path)) not in state.processed_files
                or state.processed_files[key].mtime < mtime
            )
        ]

    @staticmethod
    async def process_transcript(
        path: Path,
        classifier: InferenceEngine,
        scored_buckets: frozenset[BucketKey] = frozenset(),
    ) -> list[SentimentRecord]:
        messages = TranscriptParser.parse_file(path)
        if not messages:
            return []

        all_buckets = ConversationBucketer.bucket_messages(messages)
        new_buckets = [
            b for b in all_buckets
            if BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) not in scored_buckets
        ]

        if not new_buckets:
            return []

        scores = await classifier.score(new_buckets)

        return [
            SentimentRecord(
                time=bucket.bucket_start,
                conversation_id=bucket.session_id,
                bucket_index=bucket.bucket_index,
                sentiment_score=score,
            )
            for bucket, score in zip(new_buckets, scores)
        ]

    @staticmethod
    def save_records(state: AppState, path: Path, mtime: float, records: list[SentimentRecord]) -> None:
        existing_file = state.processed_files.get(str(path))
        prev_buckets = existing_file.scored_buckets if existing_file else frozenset()
        new_bucket_keys = frozenset(
            BucketKey(session_id=r.conversation_id, bucket_index=r.bucket_index)
            for r in records
        )
        state.processed_files[str(path)] = ProcessedFile(
            mtime=mtime,
            scored_buckets=prev_buckets | new_bucket_keys,
        )
        by_session: dict[SessionId, list[SentimentRecord]] = {}
        for record in records:
            by_session.setdefault(record.conversation_id, []).append(record)
        for session_id, session_records in by_session.items():
            existing = state.sessions.get(session_id)
            merged = list(existing.records) if existing else []
            merged.extend(session_records)
            state.sessions[session_id] = ProcessedSession(
                records=tuple(merged),
                uploaded=False,
            )
        state.save()

    @classmethod
    async def run(
        cls,
        state: AppState,
        engine: str = "omlx",
        model_repo: str | None = None,
        new_transcripts: list[tuple[Path, float]] | None = None,
        on_records: Callable[[list[SentimentRecord]], None] | None = None,
    ) -> list[SentimentRecord]:
        match engine:
            case "mlx":
                from cc_sentiment.sentiment import SentimentClassifier
                classifier: InferenceEngine = (
                    SentimentClassifier(model_repo) if model_repo
                    else SentimentClassifier()
                )
            case "omlx":
                classifier = OMLXEngine(model_repo=model_repo)
                await classifier.warm_system_prompt()
            case _:
                raise ValueError(f"Unknown engine: {engine}")

        transcripts = new_transcripts or cls.discover_new_transcripts(state)
        if not transcripts:
            return []

        all_records: list[SentimentRecord] = []

        try:
            for path, mtime in transcripts:
                existing_file = state.processed_files.get(str(path))
                scored_buckets = existing_file.scored_buckets if existing_file else frozenset()
                records = await cls.process_transcript(path, classifier, scored_buckets)
                all_records.extend(records)
                cls.save_records(state, path, mtime, records)

                if on_records and records:
                    on_records(records)
        finally:
            await classifier.close()

        return all_records
