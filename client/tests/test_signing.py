from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_sentiment.signing import (
    KeyDiscovery,
    PayloadSigner,
    SSHBackend,
    SSHKeyInfo,
)
from tests.helpers import make_record


class TestKeyDiscovery:
    def test_find_ssh_keys_ed25519(self) -> None:
        with patch("cc_sentiment.signing.SSH_DIR") as mock_ssh_dir:
            ed25519_path = MagicMock(spec=Path)
            ed25519_path.exists.return_value = True
            pub_path = MagicMock()
            pub_path.read_text.return_value = "ssh-ed25519 AAAA user@host"
            ed25519_path.with_suffix.return_value = pub_path
            ed25519_path.suffix = ""

            rsa_path = MagicMock(spec=Path)
            rsa_path.exists.return_value = False

            mock_ssh_dir.__truediv__ = lambda self, name: ed25519_path if name == "id_ed25519" else rsa_path
            keys = KeyDiscovery.find_ssh_keys()
            assert len(keys) == 1
            assert keys[0].algorithm == "ssh-ed25519"

    def test_find_ssh_keys_missing(self) -> None:
        with patch("cc_sentiment.signing.SSH_DIR", Path("/nonexistent")):
            keys = KeyDiscovery.find_ssh_keys()
            assert keys == ()

    @patch("cc_sentiment.signing.httpx.get")
    def test_fetch_github_ssh_keys(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA key1\nssh-rsa BBBB key2\n"
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        keys = KeyDiscovery.fetch_github_ssh_keys("testuser")
        assert len(keys) == 2
        assert keys[0] == "ssh-ed25519 AAAA key1"

    @patch("cc_sentiment.signing.httpx.get")
    def test_fetch_github_ssh_keys_empty(self, mock_get: MagicMock) -> None:
        mock_response = MagicMock()
        mock_response.text = ""
        mock_response.raise_for_status = MagicMock()
        mock_get.return_value = mock_response

        keys = KeyDiscovery.fetch_github_ssh_keys("testuser")
        assert keys == ()

    @patch.object(KeyDiscovery, "fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",))
    @patch.object(KeyDiscovery, "find_ssh_keys")
    def test_match_ssh_key_success(self, mock_find: MagicMock, mock_fetch: MagicMock) -> None:
        mock_find.return_value = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment=""),)
        with patch.object(SSHBackend, "fingerprint", return_value="ssh-ed25519 AAAA"):
            result = KeyDiscovery.match_ssh_key("testuser")

        assert result is not None
        assert result.private_key_path == Path("/home/.ssh/id_ed25519")

    @patch.object(KeyDiscovery, "fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",))
    @patch.object(KeyDiscovery, "find_ssh_keys")
    def test_match_ssh_key_no_match(self, mock_find: MagicMock, mock_fetch: MagicMock) -> None:
        mock_find.return_value = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment=""),)
        with patch.object(SSHBackend, "fingerprint", return_value="ssh-ed25519 CCCC"):
            result = KeyDiscovery.match_ssh_key("testuser")

        assert result is None

    @patch.object(KeyDiscovery, "fetch_github_ssh_keys", return_value=())
    def test_match_ssh_key_no_remote_keys(self, mock_fetch: MagicMock) -> None:
        result = KeyDiscovery.match_ssh_key("testuser")
        assert result is None

    def test_has_tool(self) -> None:
        assert KeyDiscovery.has_tool("python3") is True
        assert KeyDiscovery.has_tool("nonexistent_tool_xyz") is False


class TestPayloadSigner:
    def test_canonical_json_deterministic(self) -> None:
        records = [make_record(), make_record(bucket_index=1, score=3)]
        result = PayloadSigner.canonical_json(records)
        parsed = json.loads(result)
        assert len(parsed) == 2

    def test_canonical_json_sorted_keys(self) -> None:
        records = [make_record()]
        result = PayloadSigner.canonical_json(records)
        parsed = json.loads(result)
        keys = list(parsed[0].keys())
        assert keys == sorted(keys)

    def test_canonical_json_no_whitespace(self) -> None:
        records = [make_record()]
        result = PayloadSigner.canonical_json(records)
        assert ": " not in result
        assert ", " not in result

    def test_sign_delegates_to_backend(self) -> None:
        backend = MagicMock()
        backend.sign.return_value = "fake-signature"
        result = PayloadSigner.sign("test-data", backend)
        assert result == "fake-signature"
        backend.sign.assert_called_once_with("test-data")
