from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from cc_sentiment.signing import (
    GPGKeyInfo,
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

    def test_parse_armored_fingerprints_extracts_all_keys(self) -> None:
        armor = (Path(__file__).parent / "fixtures" / "two_gpg_keys.asc").read_text()
        fprs = KeyDiscovery.parse_armored_fingerprints(armor)
        assert "DFB010ADAD7C0FB9478A2C29DCDEBE62BE2C9A3D" in fprs
        assert "F1B9460D42F7A68B0EAC73F07BAE3C9D5A4A029A" in fprs
        assert len(fprs) == 2

    def test_parse_armored_fingerprints_empty_armor(self) -> None:
        assert KeyDiscovery.parse_armored_fingerprints("") == frozenset()

    def test_parse_armored_fingerprints_does_not_pollute_keyring(self, tmp_path: Path) -> None:
        import gnupg
        armor = (Path(__file__).parent / "fixtures" / "two_gpg_keys.asc").read_text()

        gpg_home = tmp_path / "gnupg"
        gpg_home.mkdir(mode=0o700)

        real_gpg_cls = gnupg.GPG
        def make_gpg(*args, **kwargs):
            kwargs.setdefault("gnupghome", str(gpg_home))
            return real_gpg_cls(*args, **kwargs)

        with patch("cc_sentiment.signing.gnupg.GPG", side_effect=make_gpg):
            KeyDiscovery.parse_armored_fingerprints(armor)

        keys = real_gpg_cls(gnupghome=str(gpg_home)).list_keys()
        assert list(keys) == []

    @patch.object(KeyDiscovery, "fetch_github_gpg_keys")
    @patch.object(KeyDiscovery, "find_gpg_keys")
    @patch.object(KeyDiscovery, "parse_armored_fingerprints")
    def test_match_gpg_key_finds_local_key_published_on_github(
        self, mock_parse: MagicMock, mock_find: MagicMock, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n..."
        mock_parse.return_value = frozenset(["F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"])
        mock_find.return_value = (
            GPGKeyInfo(fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A", email="me@example.com", algo="rsa4096"),
        )
        result = KeyDiscovery.match_gpg_key("testuser")
        assert result is not None
        assert result.fpr == "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"

    @patch.object(KeyDiscovery, "fetch_github_gpg_keys")
    @patch.object(KeyDiscovery, "find_gpg_keys")
    @patch.object(KeyDiscovery, "parse_armored_fingerprints")
    def test_match_gpg_key_returns_none_when_no_overlap(
        self, mock_parse: MagicMock, mock_find: MagicMock, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n..."
        mock_parse.return_value = frozenset(["AAAA1111AAAA1111AAAA1111AAAA1111AAAA1111"])
        mock_find.return_value = (
            GPGKeyInfo(fpr="BBBB2222BBBB2222BBBB2222BBBB2222BBBB2222", email="me@example.com", algo="rsa4096"),
        )
        assert KeyDiscovery.match_gpg_key("testuser") is None

    @patch.object(KeyDiscovery, "fetch_github_gpg_keys", return_value="")
    def test_match_gpg_key_returns_none_when_github_empty(self, mock_fetch: MagicMock) -> None:
        assert KeyDiscovery.match_gpg_key("testuser") is None

    @patch.object(KeyDiscovery, "fetch_github_gpg_keys")
    @patch.object(KeyDiscovery, "parse_armored_fingerprints")
    def test_gpg_key_on_github_returns_true_when_published(
        self, mock_parse: MagicMock, mock_fetch: MagicMock
    ) -> None:
        mock_fetch.return_value = "-----BEGIN PGP PUBLIC KEY BLOCK-----\n..."
        mock_parse.return_value = frozenset(["F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"])
        assert KeyDiscovery.gpg_key_on_github("testuser", "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A") is True

    @patch.object(KeyDiscovery, "fetch_github_gpg_keys", return_value="")
    def test_gpg_key_on_github_returns_false_when_github_empty(self, mock_fetch: MagicMock) -> None:
        assert KeyDiscovery.gpg_key_on_github("testuser", "ABCD1234") is False


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


class TestKeyGeneration:
    def test_generate_ssh_key(self) -> None:
        with patch("cc_sentiment.signing.SSH_DIR", Path("/home/.ssh")), \
             patch("cc_sentiment.signing.subprocess.run") as mock_run, \
             patch("pathlib.Path.read_text", return_value="ssh-ed25519 AAAA cc-sentiment\n"), \
             patch("pathlib.Path.exists", return_value=True):
            mock_run.return_value = MagicMock(returncode=0)
            result = KeyDiscovery.generate_ssh_key()
            assert result.algorithm == "ssh-ed25519"
            assert result.comment == "cc-sentiment"
            assert result.path == Path("/home/.ssh/id_ed25519")
            mock_run.assert_called_once()

    def test_upload_github_ssh_key_success(self) -> None:
        key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.signing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            assert KeyDiscovery.upload_github_ssh_key(key) is True

    def test_upload_github_ssh_key_failure(self) -> None:
        key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.signing.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            assert KeyDiscovery.upload_github_ssh_key(key) is False

    def test_upload_github_gpg_key_success(self) -> None:
        key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
        with patch("cc_sentiment.signing.gnupg.GPG") as mock_gpg_cls, \
             patch("cc_sentiment.signing.subprocess.run") as mock_run, \
             patch("cc_sentiment.signing.tempfile.NamedTemporaryFile"), \
             patch("pathlib.Path.unlink"):
            mock_gpg_cls.return_value.export_keys.return_value = "-----BEGIN PGP PUBLIC KEY BLOCK-----"
            mock_run.return_value = MagicMock(returncode=0)
            assert KeyDiscovery.upload_github_gpg_key(key) is True

    def test_upload_github_gpg_key_no_export(self) -> None:
        key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")
        with patch("cc_sentiment.signing.gnupg.GPG") as mock_gpg_cls:
            mock_gpg_cls.return_value.export_keys.return_value = ""
            assert KeyDiscovery.upload_github_gpg_key(key) is False
