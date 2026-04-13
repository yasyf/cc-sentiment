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
