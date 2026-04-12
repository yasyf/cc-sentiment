from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from models import SentimentRecord
from verify import Verifier


class TestFetchGithubKeys:
    def test_returns_keys(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\nssh-rsa AAAA2 user@host\n"
        mock_response.raise_for_status = MagicMock()

        with patch("verify.httpx.get", return_value=mock_response) as mock_get:
            keys = Verifier().fetch_github_keys("octocat")

        mock_get.assert_called_once_with("https://github.com/octocat.keys")
        assert keys == ["ssh-ed25519 AAAA1 user@host", "ssh-rsa AAAA2 user@host"]

    def test_filters_empty_lines(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\n\n\nssh-rsa AAAA2 user@host\n"
        mock_response.raise_for_status = MagicMock()

        with patch("verify.httpx.get", return_value=mock_response):
            keys = Verifier().fetch_github_keys("octocat")

        assert len(keys) == 2


class TestVerifySignature:
    def test_success_with_matching_key(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\n"
        mock_response.raise_for_status = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("verify.httpx.get", return_value=mock_response),
            patch("verify.subprocess.run", return_value=mock_result) as mock_run,
        ):
            assert Verifier().verify_signature("octocat", '{"data":"test"}', "sig-content")

        args = mock_run.call_args[0][0]
        assert args[0] == "ssh-keygen"
        assert "-Y" in args
        assert "verify" in args

    def test_failure_with_no_matching_key(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\nssh-rsa AAAA2 user@host\n"
        mock_response.raise_for_status = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 1

        with (
            patch("verify.httpx.get", return_value=mock_response),
            patch("verify.subprocess.run", return_value=mock_result),
        ):
            assert not Verifier().verify_signature("octocat", '{"data":"test"}', "bad-sig")

    def test_no_shell_true(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\n"
        mock_response.raise_for_status = MagicMock()

        mock_result = MagicMock()
        mock_result.returncode = 0

        with (
            patch("verify.httpx.get", return_value=mock_response),
            patch("verify.subprocess.run", return_value=mock_result) as mock_run,
        ):
            Verifier().verify_signature("octocat", "{}", "sig")

        assert "shell" not in mock_run.call_args.kwargs or not mock_run.call_args.kwargs["shell"]


class TestCanonicalJson:
    def test_deterministic_output(self) -> None:
        records = [
            SentimentRecord(
                time=datetime(2026, 4, 12, 10, 30, tzinfo=timezone.utc),
                conversation_id="abc-123",
                bucket_index=0,
                sentiment_score=4,
                prompt_version="v1",
                model_id="gemma-4-e4b-it-4bit",
                client_version="0.1.0",
            ),
            SentimentRecord(
                time=datetime(2026, 4, 12, 11, 0, tzinfo=timezone.utc),
                conversation_id="abc-123",
                bucket_index=1,
                sentiment_score=3,
                prompt_version="v1",
                model_id="gemma-4-e4b-it-4bit",
                client_version="0.1.0",
            ),
        ]
        canonical = json.dumps(
            [r.model_dump(mode="json") for r in records],
            sort_keys=True,
            separators=(",", ":"),
        )

        assert '"bucket_index":0' in canonical
        assert '"bucket_index":1' in canonical
        assert " " not in canonical

        canonical_again = json.dumps(
            [r.model_dump(mode="json") for r in records],
            sort_keys=True,
            separators=(",", ":"),
        )
        assert canonical == canonical_again
