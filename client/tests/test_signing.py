from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import orjson

from cc_sentiment.signing import (
    GPGKeyInfo,
    KeyDiscovery,
    PayloadSigner,
    SSHKeyInfo,
)
from cc_sentiment.signing.discovery import GIST_DESCRIPTION, GIST_README_TEMPLATE
from tests.helpers import make_record


class TestKeyDiscovery:
    def test_find_ssh_keys_ed25519(self, tmp_path: Path) -> None:
        key_path = tmp_path / "id_ed25519"
        key_path.write_text("private")
        key_path.with_suffix(".pub").write_text("ssh-ed25519 AAAA user@host")
        with patch("cc_sentiment.signing.discovery.SSH_DIR", tmp_path):
            keys = KeyDiscovery.find_ssh_keys()
            assert len(keys) == 1
            assert keys[0].algorithm == "ssh-ed25519"

    def test_find_ssh_keys_missing(self) -> None:
        with patch("cc_sentiment.signing.discovery.SSH_DIR", Path("/nonexistent")):
            keys = KeyDiscovery.find_ssh_keys()
            assert keys == ()

    def test_find_ssh_keys_skips_missing_public_key(self, tmp_path: Path) -> None:
        (tmp_path / "id_ed25519").write_text("private")
        with patch("cc_sentiment.signing.discovery.SSH_DIR", tmp_path):
            assert KeyDiscovery.find_ssh_keys() == ()

    def test_has_tool(self) -> None:
        assert KeyDiscovery.has_tool("python3") is True
        assert KeyDiscovery.has_tool("nonexistent_tool_xyz") is False

    def test_gist_readme_is_concise(self) -> None:
        lowered = GIST_README_TEMPLATE.lower()
        assert "signing key" in lowered
        assert "sentiments.cc" in lowered
        assert "delete this gist" in lowered
        assert len(GIST_README_TEMPLATE.splitlines()) <= 6

    def test_gist_description_constant(self) -> None:
        assert GIST_DESCRIPTION == "cc-sentiment public key"


class TestPayloadSigner:
    def test_canonical_json_deterministic(self) -> None:
        records = [make_record(), make_record(bucket_index=1, score=3)]
        result = PayloadSigner.canonical_json(records)
        parsed = orjson.loads(result)
        assert len(parsed) == 2

    def test_canonical_json_sorted_keys(self) -> None:
        records = [make_record()]
        result = PayloadSigner.canonical_json(records)
        parsed = orjson.loads(result)
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


class TestKeyGeneration:
    def test_upload_github_ssh_key_success(self) -> None:
        key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert KeyDiscovery.upload_github_ssh_key(key) is True

    def test_upload_github_ssh_key_failure(self) -> None:
        key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert KeyDiscovery.upload_github_ssh_key(key) is False

    def test_upload_github_gpg_key_success(self) -> None:
        key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
        with patch("cc_sentiment.signing.discovery.gnupg.GPG") as mock_gpg_cls, \
             patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run, \
             patch("cc_sentiment.signing.discovery.tempfile.NamedTemporaryFile"), \
             patch("pathlib.Path.unlink"):
            mock_gpg_cls.return_value.export_keys.return_value = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
            mock_run.return_value = MagicMock(returncode=0)
            assert KeyDiscovery.upload_github_gpg_key(key) is True

    def test_upload_github_gpg_key_no_export(self) -> None:
        key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
        with patch("cc_sentiment.signing.discovery.gnupg.GPG") as mock_gpg_cls:
            mock_gpg_cls.return_value.export_keys.return_value = ""
            assert KeyDiscovery.upload_github_gpg_key(key) is False


class TestGistKeypair:
    def test_gh_authenticated_missing_gh(self) -> None:
        with patch.object(KeyDiscovery, "has_tool", return_value=False):
            assert KeyDiscovery.gh_authenticated() is False

    def test_gh_authenticated_success(self) -> None:
        with (
            patch.object(KeyDiscovery, "has_tool", return_value=True),
            patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            assert KeyDiscovery.gh_authenticated() is True

    def test_gh_authenticated_not_logged_in(self) -> None:
        with (
            patch.object(KeyDiscovery, "has_tool", return_value=True),
            patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=1)
            assert KeyDiscovery.gh_authenticated() is False

    def test_find_gist_keypair_both_present(self, tmp_path: Path) -> None:
        key = tmp_path / "id_ed25519"
        pub = tmp_path / "id_ed25519.pub"
        key.write_text("private")
        pub.write_text("ssh-ed25519 AAA cc-sentiment")
        with patch("cc_sentiment.signing.discovery.CC_SENTIMENT_KEY_DIR", tmp_path):
            assert KeyDiscovery.find_gist_keypair() == key

    def test_find_gist_keypair_missing_pub(self, tmp_path: Path) -> None:
        (tmp_path / "id_ed25519").write_text("private")
        with patch("cc_sentiment.signing.discovery.CC_SENTIMENT_KEY_DIR", tmp_path):
            assert KeyDiscovery.find_gist_keypair() is None

    def test_find_gist_keypair_missing_private(self, tmp_path: Path) -> None:
        (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAA cc-sentiment")
        with patch("cc_sentiment.signing.discovery.CC_SENTIMENT_KEY_DIR", tmp_path):
            assert KeyDiscovery.find_gist_keypair() is None

    def test_generate_managed_ssh_key_runs_ssh_keygen(self, tmp_path: Path) -> None:
        with (
            patch("cc_sentiment.signing.discovery.CC_SENTIMENT_KEY_DIR", tmp_path),
            patch.object(KeyDiscovery, "find_gist_keypair", return_value=None),
            patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
        ):
            (tmp_path / "id_ed25519").write_text("private")
            (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAAA cc-sentiment\n")
            mock_run.return_value = MagicMock(returncode=0)
            result = KeyDiscovery.generate_managed_ssh_key()

        assert result.path == tmp_path / "id_ed25519"
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["ssh-keygen", "-t", "ed25519"]
        assert "-N" in call_args and call_args[call_args.index("-N") + 1] == ""
        assert "-C" in call_args and call_args[call_args.index("-C") + 1] == "cc-sentiment"

    def test_generate_managed_ssh_key_skips_regeneration(self, tmp_path: Path) -> None:
        existing = tmp_path / "id_ed25519"
        existing.write_text("private")
        (tmp_path / "id_ed25519.pub").write_text("ssh-ed25519 AAA cc-sentiment")
        with (
            patch("cc_sentiment.signing.discovery.CC_SENTIMENT_KEY_DIR", tmp_path),
            patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
        ):
            result = KeyDiscovery.generate_managed_ssh_key()

        assert result.path == existing
        mock_run.assert_not_called()

    def test_create_gist_from_text_writes_files_and_returns_id(self) -> None:
        with patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="https://gist.github.com/octocat/abcdef1234567890abcd\n",
            )
            gist_id = KeyDiscovery.create_gist_from_text("ssh-ed25519 AAAAPUBKEY cc-sentiment")

        assert gist_id == "abcdef1234567890abcd"
        call_args = mock_run.call_args[0][0]
        assert call_args[:3] == ["gh", "gist", "create"]
        assert "--public" in call_args
        assert "-d" in call_args
        assert call_args[call_args.index("-d") + 1] == GIST_DESCRIPTION

    def test_generate_managed_gpg_key_returns_only_new_fingerprint(self, tmp_path: Path) -> None:
        existing = GPGKeyInfo(fpr="OLDFPR", email="alice@example.com", algo="rsa4096")
        new = GPGKeyInfo(fpr="NEWFPR", email="alice@example.com", algo="ed25519")

        sequence = [(existing,), (existing, new)]
        with (
            patch.object(KeyDiscovery, "find_gpg_keys", side_effect=sequence),
            patch("cc_sentiment.signing.discovery.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0)
            result = KeyDiscovery.generate_managed_gpg_key("cc-sentiment", "alice@example.com")

        assert result.fpr == "NEWFPR"
