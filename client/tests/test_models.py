from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from cc_sentiment.models import (
    AppState,
    BucketIndex,
    CLIENT_VERSION,
    ClientConfig,
    MODEL_ID,
    PROMPT_VERSION,
    ProcessedFile,
    ProcessedSession,
    SentimentRecord,
    SentimentScore,
    SessionId,
)


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


class TestAppState:
    def test_load_save_roundtrip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        record = make_record()
        state = AppState(
            sessions={
                SessionId("s1"): ProcessedSession(records=(record,)),
            },
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            state.save()
            loaded = AppState.load()
        assert loaded.sessions[SessionId("s1")].records == (record,)
        assert loaded.sessions[SessionId("s1")].uploaded is False

    def test_load_missing_file_returns_default(self, tmp_path: Path) -> None:
        with patch.object(AppState, "state_path", return_value=tmp_path / "nope.json"):
            loaded = AppState.load()
        assert loaded.sessions == {}
        assert loaded.processed_files == {}
        assert loaded.config is None


class TestProcessedSession:
    def test_frozen_immutability(self) -> None:
        session = ProcessedSession(records=(make_record(),))
        with pytest.raises(ValidationError):
            session.uploaded = True


class TestSentimentRecord:
    def test_serialization_roundtrip(self) -> None:
        record = make_record()
        data = record.model_dump(mode="json")
        restored = SentimentRecord.model_validate(data)
        assert restored == record
        assert restored.prompt_version == PROMPT_VERSION
        assert restored.model_id == MODEL_ID
        assert restored.client_version == CLIENT_VERSION


class TestProcessedFile:
    def test_frozen(self) -> None:
        pf = ProcessedFile(mtime=1234.5)
        with pytest.raises(ValidationError):
            pf.mtime = 9999.0


class TestClientConfig:
    def test_path_serialization(self) -> None:
        config = ClientConfig(
            github_username="testuser",
            key_path=Path("/home/.ssh/id_ed25519"),
        )
        data = config.model_dump(mode="json")
        restored = ClientConfig.model_validate(data)
        assert restored.key_path == Path("/home/.ssh/id_ed25519")
        assert restored.github_username == "testuser"


class TestNewTypes:
    def test_session_id(self) -> None:
        record = make_record(session_id="my-session")
        assert record.conversation_id == SessionId("my-session")

    def test_bucket_index(self) -> None:
        record = make_record(bucket_index=3)
        assert record.bucket_index == BucketIndex(3)

    def test_sentiment_score(self) -> None:
        record = make_record(score=5)
        assert record.sentiment_score == SentimentScore(5)
