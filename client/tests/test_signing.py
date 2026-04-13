from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from cc_sentiment.models import (
    BucketIndex,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from cc_sentiment.signing import KeyDiscovery, PayloadSigner


class TestKeyDiscovery:
    def test_find_private_key_ed25519(self) -> None:
        with patch("cc_sentiment.signing.SSH_DIR") as mock_ssh_dir:
            ed25519_path = MagicMock(spec=Path)
            ed25519_path.exists.return_value = True
            mock_ssh_dir.__truediv__ = MagicMock(return_value=ed25519_path)
            key = KeyDiscovery.find_private_key()
            assert key == ed25519_path

    def test_find_private_key_missing(self) -> None:
        with patch("cc_sentiment.signing.SSH_DIR", Path("/nonexistent")):
            with pytest.raises(FileNotFoundError):
                KeyDiscovery.find_private_key()

    @patch("cc_sentiment.signing.httpx.get")
    def test_fetch_github_keys(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA key1\nssh-rsa BBBB key2\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        keys = KeyDiscovery.fetch_github_keys("testuser")
        assert len(keys) == 2
        assert keys[0] == "ssh-ed25519 AAAA key1"
        mock_get.assert_called_once_with(
            "https://github.com/testuser.keys", timeout=10.0
        )

    @patch("cc_sentiment.signing.httpx.get")
    @patch.object(
        KeyDiscovery, "read_public_key", return_value="ssh-ed25519 AAAA localkey"
    )
    @patch.object(
        KeyDiscovery, "find_private_key", return_value=Path("/home/.ssh/id_ed25519")
    )
    def test_match_github_key_success(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA key1\nssh-rsa BBBB key2\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        key_path = KeyDiscovery.match_github_key("testuser")
        assert key_path == Path("/home/.ssh/id_ed25519")

    @patch("cc_sentiment.signing.httpx.get")
    @patch.object(
        KeyDiscovery, "read_public_key", return_value="ssh-ed25519 CCCC nomatch"
    )
    @patch.object(
        KeyDiscovery, "find_private_key", return_value=Path("/home/.ssh/id_ed25519")
    )
    def test_match_github_key_no_match(
        self,
        mock_find: MagicMock,
        mock_read: MagicMock,
        mock_get: MagicMock,
    ) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA key1\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        with pytest.raises(ValueError, match="No local SSH key matches"):
            KeyDiscovery.match_github_key("testuser")


class TestPayloadSigner:
    def test_canonical_json_deterministic(self) -> None:
        records = [
            SentimentRecord(
                time=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
                conversation_id=SessionId("session-1"),
                bucket_index=BucketIndex(0),
                sentiment_score=SentimentScore(4),
            ),
            SentimentRecord(
                time=datetime(2026, 4, 10, 7, 40, 0, tzinfo=timezone.utc),
                conversation_id=SessionId("session-1"),
                bucket_index=BucketIndex(1),
                sentiment_score=SentimentScore(3),
            ),
        ]
        result = PayloadSigner.canonical_json(records)
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_canonical_json_sorted_keys(self) -> None:
        records = [
            SentimentRecord(
                time=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
                conversation_id=SessionId("session-1"),
                bucket_index=BucketIndex(0),
                sentiment_score=SentimentScore(4),
            ),
        ]
        result = PayloadSigner.canonical_json(records)
        parsed = json.loads(result)
        keys = list(parsed[0].keys())
        assert keys == sorted(keys)

    def test_canonical_json_no_whitespace(self) -> None:
        records = [
            SentimentRecord(
                time=datetime(2026, 4, 10, 7, 35, 0, tzinfo=timezone.utc),
                conversation_id=SessionId("session-1"),
                bucket_index=BucketIndex(0),
                sentiment_score=SentimentScore(4),
            ),
        ]
        result = PayloadSigner.canonical_json(records)
        assert ": " not in result
        assert ", " not in result
