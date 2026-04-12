from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import anyio

from client.memory import MemoryProbe
from client.models import (
    AppState,
    ProcessedSession,
    SentimentRecord,
    SessionId,
)
from client.transcripts import (
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
                (session_id := SessionId(path.stem)) not in state.processed
                or state.processed[session_id].mtime < mtime
            )
        ]

    @staticmethod
    def process_transcript(path: Path) -> list[SentimentRecord]:
        from client.sentiment import SentimentClassifier

        messages = TranscriptParser.parse_file(path)
        if not messages:
            return []

        buckets = ConversationBucketer.bucket_messages(messages)
        classifier = SentimentClassifier()
        scores = classifier.score_buckets(buckets)

        return [
            SentimentRecord(
                time=bucket.bucket_start,
                conversation_id=bucket.session_id,
                bucket_index=bucket.bucket_index,
                sentiment_score=score,
            )
            for bucket, score in zip(buckets, scores)
        ]

    @classmethod
    async def run(cls, state: AppState) -> list[SentimentRecord]:
        new_transcripts = cls.discover_new_transcripts(state)
        if not new_transcripts:
            return []

        all_records: list[SentimentRecord] = []
        batch_size = MemoryProbe.optimal_batch_size()

        for i in range(0, len(new_transcripts), batch_size):
            batch = new_transcripts[i : i + batch_size]
            for path, mtime in batch:
                records = await anyio.to_thread.run_sync(
                    lambda p=path: cls.process_transcript(p)
                )
                all_records.extend(records)

                session_id = SessionId(path.stem)
                state.processed[session_id] = ProcessedSession(
                    mtime=mtime,
                    buckets=len(records),
                    uploaded=False,
                )
                state.save()

        return all_records
