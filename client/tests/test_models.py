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
    ContributorId,
    DEFAULT_MODEL,
    GPGConfig,
    PROMPT_VERSION,
    ProcessedFile,
    ProcessedSession,
    SentimentRecord,
    SentimentScore,
    SessionId,
    SSHConfig,
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
        assert restored.inference_model == DEFAULT_MODEL
        assert restored.client_version == CLIENT_VERSION

    def test_wire_format_uses_model_id_key(self) -> None:
        record = make_record()
        data = record.model_dump(mode="json", by_alias=True)
        assert "model_id" in data
        assert "inference_model" not in data
        restored = SentimentRecord.model_validate(data)
        assert restored.inference_model == DEFAULT_MODEL


class TestProcessedFile:
    def test_frozen(self) -> None:
        pf = ProcessedFile(mtime=1234.5)
        with pytest.raises(ValidationError):
            pf.mtime = 9999.0


class TestClientConfig:
    def test_ssh_config_serialization(self) -> None:
        config = SSHConfig(
            contributor_id=ContributorId("testuser"),
            key_path=Path("/home/.ssh/id_ed25519"),
        )
        data = config.model_dump(mode="json")
        assert data["key_type"] == "ssh"
        assert data["contributor_type"] == "github"
        restored = SSHConfig.model_validate(data)
        assert restored.key_path == Path("/home/.ssh/id_ed25519")
        assert restored.contributor_id == ContributorId("testuser")

    def test_gpg_config_github_serialization(self) -> None:
        config = GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId("testuser"),
            fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
        )
        data = config.model_dump(mode="json")
        assert data["key_type"] == "gpg"
        assert data["contributor_type"] == "github"
        restored = GPGConfig.model_validate(data)
        assert restored.fpr == "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"

    def test_gpg_config_openpgp_serialization(self) -> None:
        fpr = "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"
        config = GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId(fpr),
            fpr=fpr,
        )
        data = config.model_dump(mode="json")
        assert data["contributor_type"] == "gpg"
        assert data["contributor_id"] == fpr
        restored = GPGConfig.model_validate(data)
        assert restored.contributor_type == "gpg"

    def test_state_roundtrip_with_ssh_config(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = AppState(
            config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")),
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            state.save()
            loaded = AppState.load()
        assert isinstance(loaded.config, SSHConfig)
        assert loaded.config.key_path == Path("/home/.ssh/id_ed25519")

    def test_state_roundtrip_with_gpg_config(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = AppState(
            config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCDEF1234567890"),
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            state.save()
            loaded = AppState.load()
        assert isinstance(loaded.config, GPGConfig)
        assert loaded.config.fpr == "ABCDEF1234567890"


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
