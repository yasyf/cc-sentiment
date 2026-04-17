from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from cc_sentiment.models import (
    AppState,
    BucketIndex,
    CLIENT_VERSION,
    ContributorId,
    GistConfig,
    GPGConfig,
    PROMPT_VERSION,
    SentimentRecord,
    SentimentScore,
    SessionId,
    SSHConfig,
)
from tests.helpers import make_record


class TestAppState:
    def test_load_save_roundtrip(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=Path("/home/.ssh/id_ed25519"),
            ),
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            state.save()
            loaded = AppState.load()
        assert isinstance(loaded.config, SSHConfig)
        assert loaded.config.contributor_id == ContributorId("testuser")

    def test_load_missing_file_returns_default(self, tmp_path: Path) -> None:
        with patch.object(AppState, "state_path", return_value=tmp_path / "nope.json"):
            loaded = AppState.load()
        assert loaded.config is None

    def test_load_ignores_unknown_legacy_keys(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state_file.write_text(
            '{"config": null, "sessions": {"s1": {"records": []}}, "processed_files": {}}'
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            loaded = AppState.load()
        assert loaded.config is None


class TestSentimentRecord:
    def test_serialization_roundtrip(self) -> None:
        record = make_record(claude_model="claude-sonnet-4-20250514")
        data = record.model_dump(mode="json")
        restored = SentimentRecord.model_validate(data)
        assert restored == record
        assert restored.prompt_version == PROMPT_VERSION
        assert restored.claude_model == "claude-sonnet-4-20250514"
        assert restored.client_version == CLIENT_VERSION


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

    def test_gist_config_serialization(self) -> None:
        config = GistConfig(
            contributor_id=ContributorId("octocat"),
            key_path=Path("/home/.cc-sentiment/keys/id_ed25519"),
            gist_id="abcdef1234567890abcd",
        )
        data = config.model_dump(mode="json")
        assert data["key_type"] == "gist"
        assert data["contributor_type"] == "gist"
        assert data["gist_id"] == "abcdef1234567890abcd"
        restored = GistConfig.model_validate(data)
        assert restored.gist_id == "abcdef1234567890abcd"
        assert restored.key_path == Path("/home/.cc-sentiment/keys/id_ed25519")

    def test_state_roundtrip_with_gist_config(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        state = AppState(
            config=GistConfig(
                contributor_id=ContributorId("octocat"),
                key_path=Path("/home/.cc-sentiment/keys/id_ed25519"),
                gist_id="abcdef1234567890abcd",
            ),
        )
        with patch.object(AppState, "state_path", return_value=state_file):
            state.save()
            loaded = AppState.load()
        assert isinstance(loaded.config, GistConfig)
        assert loaded.config.gist_id == "abcdef1234567890abcd"
        assert loaded.config.contributor_id == ContributorId("octocat")


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
