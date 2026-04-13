from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from cc_sentiment.models import (
    AppState,
    BucketIndex,
    BucketKey,
    ProcessedFile,
    ProcessedSession,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.pipeline import Pipeline

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "sample_transcript.jsonl"


def make_record(
    session_id: str = "session-1",
    bucket_index: int = 0,
    score: int = 4,
) -> SentimentRecord:
    return SentimentRecord(
        time=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
        conversation_id=SessionId(session_id),
        bucket_index=BucketIndex(bucket_index),
        sentiment_score=SentimentScore(score),
    )


class TestDiscoverNewTranscripts:
    def test_finds_new_files(self) -> None:
        state = AppState()
        fake_path = Path("/fake/transcript.jsonl")
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[fake_path]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=100.0):
            result = Pipeline.discover_new_transcripts(state)
        assert len(result) == 1
        assert result[0] == (fake_path, 100.0)

    def test_skips_unchanged_files(self) -> None:
        state = AppState(
            processed_files={"/fake/transcript.jsonl": ProcessedFile(mtime=100.0)},
        )
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[Path("/fake/transcript.jsonl")]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=100.0):
            result = Pipeline.discover_new_transcripts(state)
        assert result == []

    def test_reprocesses_updated_files(self) -> None:
        state = AppState(
            processed_files={"/fake/transcript.jsonl": ProcessedFile(mtime=100.0)},
        )
        with patch("cc_sentiment.transcripts.TranscriptDiscovery.find_transcripts", return_value=[Path("/fake/transcript.jsonl")]), \
             patch("cc_sentiment.transcripts.TranscriptDiscovery.transcript_mtime", return_value=200.0):
            result = Pipeline.discover_new_transcripts(state)
        assert len(result) == 1
        assert result[0][1] == 200.0


class TestProcessTranscript:
    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        empty_file = tmp_path / "empty.jsonl"
        empty_file.write_text("")
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[])
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(empty_file, classifier)

        result = anyio.run(run)
        assert result == []
        classifier.score.assert_not_called()

    def test_correct_record_count(self) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 5)
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier)

        result = anyio.run(run)
        assert len(result) == 5
        classifier.score.assert_called_once()


class TestBucketCaching:
    def test_skips_cached_buckets(self) -> None:
        cached = frozenset({BucketKey(session_id=SessionId("s1"), bucket_index=BucketIndex(0))})
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 4)
        classifier.close = AsyncMock()

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier, scored_buckets=cached)

        result = anyio.run(run)
        called_buckets = classifier.score.call_args[0][0]
        assert all(
            BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) not in cached
            for b in called_buckets
        )

    def test_all_cached_returns_empty(self) -> None:
        classifier = MagicMock()
        classifier.score = AsyncMock(return_value=[SentimentScore(3)] * 5)
        classifier.close = AsyncMock()

        async def get_all_keys() -> frozenset[BucketKey]:
            from cc_sentiment.transcripts import ConversationBucketer, TranscriptParser
            messages = TranscriptParser.parse_file(FIXTURE_PATH)
            buckets = ConversationBucketer.bucket_messages(messages)
            return frozenset(BucketKey(session_id=b.session_id, bucket_index=b.bucket_index) for b in buckets)

        all_keys = anyio.run(get_all_keys)

        async def run() -> list[SentimentRecord]:
            return await Pipeline.process_transcript(FIXTURE_PATH, classifier, scored_buckets=all_keys)

        result = anyio.run(run)
        assert result == []
        classifier.score.assert_not_called()

    def test_save_records_persists_bucket_keys(self) -> None:
        state = AppState()
        record = make_record()
        path = Path("/fake.jsonl")

        with patch.object(AppState, "save"):
            Pipeline.save_records(state, path, 100.0, [record])

        pf = state.processed_files[str(path)]
        assert BucketKey(session_id=SessionId("session-1"), bucket_index=BucketIndex(0)) in pf.scored_buckets

    def test_save_records_merges_bucket_keys(self) -> None:
        existing_key = BucketKey(session_id=SessionId("old"), bucket_index=BucketIndex(99))
        state = AppState(
            processed_files={"/fake.jsonl": ProcessedFile(mtime=50.0, scored_buckets=frozenset({existing_key}))},
        )
        record = make_record()
        path = Path("/fake.jsonl")

        with patch.object(AppState, "save"):
            Pipeline.save_records(state, path, 100.0, [record])

        pf = state.processed_files[str(path)]
        assert existing_key in pf.scored_buckets
        assert BucketKey(session_id=SessionId("session-1"), bucket_index=BucketIndex(0)) in pf.scored_buckets


class TestPipelineStateUpdate:
    def test_state_updated_with_records(self) -> None:
        state = AppState()
        record = make_record()

        mock_classifier = MagicMock()
        mock_classifier.score = AsyncMock(return_value=[])
        mock_classifier.close = AsyncMock()

        mock_sentiment_mod = MagicMock()
        mock_sentiment_mod.SentimentClassifier.return_value = mock_classifier

        with patch.dict(sys.modules, {"cc_sentiment.sentiment": mock_sentiment_mod}), \
             patch.object(Pipeline, "discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 100.0)]), \
             patch.object(Pipeline, "process_transcript", new_callable=AsyncMock, return_value=[record]), \
             patch.object(AppState, "save"):

            async def do_run() -> list[SentimentRecord]:
                return await Pipeline.run(state, engine="mlx")

            result = anyio.run(do_run)

        assert SessionId("session-1") in state.sessions
        assert state.sessions[SessionId("session-1")].records == (record,)
        assert state.sessions[SessionId("session-1")].uploaded is False
        assert str(Path("/fake.jsonl")) in state.processed_files
        assert len(result) == 1
