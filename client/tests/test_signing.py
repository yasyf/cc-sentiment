from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from client.models import (
    BucketIndex,
    SentimentRecord,
    SentimentScore,
    SessionId,
)
from client.signing import KeyDiscovery, PayloadSigner

from datetime import datetime, timezone


class TestKeyDiscovery:
    @patch("client.signing.SSH_DIR", new_callable=lambda: type("", (), {"__truediv__": lambda s, n: Path(f"/tmp/test_ssh/{n}")})())
    def test_find_private_key_ed25519(self, tmp_path: Path) -> None:
        with patch.object(Path, "exists") as mock_exists:
            mock_exists.return_value = True
            key = KeyDiscovery.find_private_key()
            assert "id_ed25519" in str(key)

    def test_find_private_key_missing(self) -> None:
        with patch("client.signing.SSH_DIR", Path("/nonexistent")):
            try:
                KeyDiscovery.find_private_key()
                assert False, "Should have raised"
            except FileNotFoundError:
                pass

    @patch("client.signing.httpx.get")
    def test_fetch_github_keys(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA key1\nssh-rsa BBBB key2\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        keys = KeyDiscovery.fetch_github_keys("testuser")
        assert len(keys) == 2
        assert keys[0] == "ssh-ed25519 AAAA key1"
        mock_get.assert_called_once_with("https://github.com/testuser.keys")

    @patch("client.signing.httpx.get")
    @patch.object(KeyDiscovery, "read_public_key", return_value="ssh-ed25519 AAAA localkey")
    @patch.object(KeyDiscovery, "find_private_key", return_value=Path("/home/.ssh/id_ed25519"))
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

    @patch("client.signing.httpx.get")
    @patch.object(KeyDiscovery, "read_public_key", return_value="ssh-ed25519 CCCC nomatch")
    @patch.object(KeyDiscovery, "find_private_key", return_value=Path("/home/.ssh/id_ed25519"))
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

        try:
            KeyDiscovery.match_github_key("testuser")
            assert False, "Should have raised"
        except ValueError as e:
            assert "No local SSH key matches" in str(e)


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
        assert " " not in result.replace(" ", "x").replace("x", "")

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
