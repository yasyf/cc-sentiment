from __future__ import annotations

import gc
import warnings
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cc_sentiment_server.verify import DictKeyCache, Verifier


class TestFetchGitHubSSHKeys:
    @pytest.mark.asyncio
    async def test_returns_keys(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\nssh-rsa AAAA2 user@host\n"
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=mock_client):
            keys = await Verifier().fetch_github_ssh_keys("octocat")

        mock_client.get.assert_called_once_with("https://github.com/octocat.keys", timeout=10.0)
        assert keys == ["ssh-ed25519 AAAA1 user@host", "ssh-rsa AAAA2 user@host"]

    @pytest.mark.asyncio
    async def test_filters_empty_lines(self) -> None:
        mock_response = MagicMock()
        mock_response.text = "ssh-ed25519 AAAA1 user@host\n\n\nssh-rsa AAAA2 user@host\n"
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=mock_client):
            keys = await Verifier().fetch_github_ssh_keys("octocat")

        assert len(keys) == 2

    @pytest.mark.asyncio
    async def test_404_raises_value_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=mock_client):
            with pytest.raises(ValueError, match="GitHub user not found"):
                await Verifier().fetch_github_ssh_keys("nonexistent-xyz")


class TestContributorTypeRouting:
    @pytest.mark.asyncio
    async def test_github_ssh_dispatches_correctly(self) -> None:
        verifier = Verifier()
        verifier.verify_github_ssh = AsyncMock(return_value=True)

        result = await verifier.verify_signature(
            "github", "octocat", '{"test":1}', "-----BEGIN SSH SIGNATURE-----\nsig"
        )

        assert result is True
        verifier.verify_github_ssh.assert_called_once_with("octocat", '{"test":1}', "-----BEGIN SSH SIGNATURE-----\nsig")

    @pytest.mark.asyncio
    async def test_github_gpg_dispatches_correctly(self) -> None:
        verifier = Verifier()
        verifier.verify_github_gpg = AsyncMock(return_value=True)

        result = await verifier.verify_signature(
            "github", "octocat", '{"test":1}', "-----BEGIN PGP SIGNATURE-----\nsig"
        )

        assert result is True
        verifier.verify_github_gpg.assert_called_once_with("octocat", '{"test":1}', "-----BEGIN PGP SIGNATURE-----\nsig")

    @pytest.mark.asyncio
    async def test_gpg_dispatches_to_openpgp(self) -> None:
        verifier = Verifier()
        verifier.verify_openpgp = AsyncMock(return_value=True)

        result = await verifier.verify_signature(
            "gpg", "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A",
            '{"test":1}', "-----BEGIN PGP SIGNATURE-----\nsig"
        )

        assert result is True
        verifier.verify_openpgp.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_contributor_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown contributor type"):
            await Verifier().verify_signature("twitter", "user", "{}", "sig")

    @pytest.mark.asyncio
    async def test_gist_dispatches_to_verify_gist(self) -> None:
        verifier = Verifier()
        verifier.verify_gist = AsyncMock(return_value=True)

        result = await verifier.verify_signature(
            "gist", "octocat/abcdef1234567890abcd", '{"test":1}', "sig"
        )

        assert result is True
        verifier.verify_gist.assert_called_once_with(
            "octocat/abcdef1234567890abcd", '{"test":1}', "sig"
        )

    @pytest.mark.asyncio
    async def test_unknown_signature_format_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown signature format"):
            await Verifier().verify_signature("github", "octocat", "{}", "garbage-sig")


class TestGitHubSSHVerification:
    @pytest.mark.asyncio
    async def test_invalid_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub username"):
            await Verifier().verify_github_ssh("bad\nuser", "{}", "sig")

    @pytest.mark.asyncio
    async def test_spaces_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub username"):
            await Verifier().verify_github_ssh("bad user", "{}", "sig")

    @pytest.mark.asyncio
    async def test_success_with_matching_key(self) -> None:
        verifier = Verifier()
        verifier.fetch_github_ssh_keys = AsyncMock(return_value=["ssh-ed25519 AAAA1 user@host"])

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            result = await verifier.verify_github_ssh("octocat", '{"test":1}', "sig")

        assert result is True

    @pytest.mark.asyncio
    async def test_failure_with_no_matching_key(self) -> None:
        verifier = Verifier()
        verifier.fetch_github_ssh_keys = AsyncMock(return_value=["ssh-ed25519 AAAA1 user@host"])

        with patch.object(verifier, "_verify_with_ssh_key", return_value=False):
            result = await verifier.verify_github_ssh("octocat", '{"test":1}', "bad")

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_keys_returns_false(self) -> None:
        verifier = Verifier()
        verifier.fetch_github_ssh_keys = AsyncMock(return_value=[])

        result = await verifier.verify_github_ssh("octocat", "{}", "sig")
        assert result is False


class TestGitHubGPGVerification:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        verifier = Verifier()
        verifier.fetch_github_gpg_keys = AsyncMock(return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey")

        with patch.object(verifier, "_check_gpg_signature", return_value=True):
            result = await verifier.verify_github_gpg("octocat", '{"test":1}', "sig")

        assert result is True

    @pytest.mark.asyncio
    async def test_no_keys_returns_false(self) -> None:
        verifier = Verifier()
        verifier.fetch_github_gpg_keys = AsyncMock(return_value="")

        result = await verifier.verify_github_gpg("octocat", "{}", "sig")
        assert result is False

    @pytest.mark.asyncio
    async def test_invalid_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub username"):
            await Verifier().verify_github_gpg("bad\nuser", "{}", "sig")


class TestOpenPGPVerification:
    @pytest.mark.asyncio
    async def test_success(self) -> None:
        verifier = Verifier()
        verifier.fetch_openpgp_key = AsyncMock(return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey")

        with patch.object(verifier, "_check_gpg_signature", return_value=True):
            result = await verifier.verify_openpgp(
                "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A", '{"test":1}', "sig"
            )

        assert result is True

    @pytest.mark.asyncio
    async def test_no_key_returns_false(self) -> None:
        verifier = Verifier()
        verifier.fetch_openpgp_key = AsyncMock(return_value="")

        result = await verifier.verify_openpgp("ABCDEF1234567890", "{}", "sig")
        assert result is False


class TestSSHKeyCache:
    @pytest.mark.asyncio
    async def test_populates_on_miss(self) -> None:
        cache = DictKeyCache()
        verifier = Verifier(key_cache=cache)
        verifier.fetch_github_ssh_keys = AsyncMock(return_value=["ssh-ed25519 KEY1 user@host"])

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            await verifier.verify_github_ssh("octocat", "{}", "sig")

        assert cache.d["ssh:octocat"] == ["ssh-ed25519 KEY1 user@host"]

    @pytest.mark.asyncio
    async def test_uses_cached_keys(self) -> None:
        cache = DictKeyCache()
        cache.d["ssh:octocat"] = ["ssh-ed25519 CACHED user@host"]
        verifier = Verifier(key_cache=cache)
        verifier.fetch_github_ssh_keys = AsyncMock()

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            await verifier.verify_github_ssh("octocat", "{}", "sig")

        verifier.fetch_github_ssh_keys.assert_not_called()

    @pytest.mark.asyncio
    async def test_refetches_on_mismatch(self) -> None:
        cache = DictKeyCache()
        cache.d["ssh:octocat"] = ["ssh-ed25519 OLD user@host"]
        verifier = Verifier(key_cache=cache)

        def verify_key(username, key, payload, sig):
            return key == "ssh-ed25519 NEW user@host"

        verifier.fetch_github_ssh_keys = AsyncMock(return_value=["ssh-ed25519 NEW user@host"])

        with patch.object(verifier, "_verify_with_ssh_key", side_effect=verify_key):
            result = await verifier.verify_github_ssh("octocat", "{}", "sig")

        assert result is True
        verifier.fetch_github_ssh_keys.assert_called_once()
        assert cache.d["ssh:octocat"] == ["ssh-ed25519 NEW user@host"]


class TestVerifyWithSSHKey:
    def test_no_resource_warning(self) -> None:
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always", ResourceWarning)
            with patch("cc_sentiment_server.verify.subprocess.run", return_value=MagicMock(returncode=0)):
                Verifier._verify_with_ssh_key(
                    "octocat", "ssh-ed25519 AAAA key", '{"data":"test"}', "sig",
                )
            gc.collect()

        resource_warnings = [x for x in w if issubclass(x.category, ResourceWarning)]
        assert len(resource_warnings) == 0

    def test_passes_correct_args_to_ssh_keygen(self) -> None:
        with patch("cc_sentiment_server.verify.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            Verifier._verify_with_ssh_key("octocat", "ssh-ed25519 AAAA key", "{}", "sig")

        args = mock_run.call_args[0][0]
        assert args[0] == "ssh-keygen"
        assert args[1:3] == ["-Y", "verify"]
        assert args[args.index("-I") + 1] == "octocat"
        assert args[args.index("-n") + 1] == "cc-sentiment"

    def test_no_shell_true(self) -> None:
        with patch("cc_sentiment_server.verify.subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
            Verifier._verify_with_ssh_key("octocat", "ssh-ed25519 AAAA key", "{}", "sig")

        kwargs = mock_run.call_args.kwargs
        assert "shell" not in kwargs or not kwargs["shell"]


def _mock_httpx_client(response: MagicMock) -> MagicMock:
    client = AsyncMock()
    client.get.return_value = response
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    return client


class TestFetchGist:
    @pytest.mark.asyncio
    async def test_returns_parsed_dict(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={"owner": {"login": "octocat"}, "description": "cc-sentiment public key"})

        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=_mock_httpx_client(mock_response)):
            result = await Verifier().fetch_gist("abcdef1234567890abcd")

        assert result["owner"]["login"] == "octocat"

    @pytest.mark.asyncio
    async def test_404_raises_value_error(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=_mock_httpx_client(mock_response)):
            with pytest.raises(ValueError, match="Gist not found"):
                await Verifier().fetch_gist("abcdef1234567890abcd")

    @pytest.mark.asyncio
    async def test_no_auth_header_sent(self) -> None:
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(return_value={})

        client = _mock_httpx_client(mock_response)
        with patch("cc_sentiment_server.verify.httpx.AsyncClient", return_value=client):
            await Verifier().fetch_gist("abcdef1234567890abcd")

        assert "headers" not in client.get.call_args.kwargs


class TestVerifyWithGistPubkey:
    @pytest.mark.asyncio
    async def test_dispatches_ssh_pubkey_to_ssh_verify(self) -> None:
        verifier = Verifier()
        with patch.object(
            verifier, "verify_with_ssh_key", new_callable=AsyncMock, return_value=True,
        ) as mock_ssh:
            result = await verifier.verify_with_gist_pubkey(
                "octocat",
                "ssh-ed25519 AAAA cc-sentiment",
                "{}",
                "sig",
            )
        assert result is True
        mock_ssh.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_pgp_pubkey_to_gpg_verify(self) -> None:
        verifier = Verifier()
        armor = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nblob\n"
        with patch.object(
            verifier, "check_gpg_signature", new_callable=AsyncMock, return_value=True,
        ) as mock_gpg:
            result = await verifier.verify_with_gist_pubkey(
                "octocat", armor, "{}", "sig",
            )
        assert result is True
        mock_gpg.assert_called_once()

    @pytest.mark.asyncio
    async def test_unknown_pubkey_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown gist public key format"):
            await Verifier().verify_with_gist_pubkey(
                "octocat", "garbage", "{}", "sig",
            )


class TestFetchGistPubkey:
    @pytest.mark.asyncio
    async def test_owner_mismatch_raises(self) -> None:
        verifier = Verifier()
        verifier.fetch_gist = AsyncMock(return_value={
            "owner": {"login": "someone-else"},
            "description": "cc-sentiment public key",
            "files": {"cc-sentiment.pub": {"content": "ssh-ed25519 AAAA cc-sentiment"}},
        })
        with pytest.raises(ValueError, match="not owned by"):
            await verifier.fetch_gist_pubkey("abcdef1234567890abcd", "octocat")

    @pytest.mark.asyncio
    async def test_description_mismatch_raises(self) -> None:
        verifier = Verifier()
        verifier.fetch_gist = AsyncMock(return_value={
            "owner": {"login": "octocat"},
            "description": "My notes",
            "files": {"cc-sentiment.pub": {"content": "ssh-ed25519 AAAA cc-sentiment"}},
        })
        with pytest.raises(ValueError, match="not a cc-sentiment gist"):
            await verifier.fetch_gist_pubkey("abcdef1234567890abcd", "octocat")

    @pytest.mark.asyncio
    async def test_happy_path_returns_stripped_pubkey(self) -> None:
        verifier = Verifier()
        verifier.fetch_gist = AsyncMock(return_value={
            "owner": {"login": "octocat"},
            "description": "cc-sentiment public key",
            "files": {"cc-sentiment.pub": {"content": "  ssh-ed25519 AAAAKEY cc-sentiment  \n"}},
        })
        pub = await verifier.fetch_gist_pubkey("abcdef1234567890abcd", "octocat")
        assert pub == "ssh-ed25519 AAAAKEY cc-sentiment"


class TestGistVerification:
    @pytest.mark.asyncio
    async def test_missing_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid gist contributor id"):
            await Verifier().verify_gist("octocat", "{}", "sig")

    @pytest.mark.asyncio
    async def test_invalid_username_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid GitHub username"):
            await Verifier().verify_gist("bad user/abcdef1234567890abcd", "{}", "sig")

    @pytest.mark.asyncio
    async def test_invalid_gist_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="Invalid gist id"):
            await Verifier().verify_gist("octocat/not-hex-zzz", "{}", "sig")

    @pytest.mark.asyncio
    async def test_success_when_cached_key_verifies(self) -> None:
        verifier = Verifier()
        verifier.fetch_gist_pubkey = AsyncMock(return_value="ssh-ed25519 AAAA cc-sentiment")

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            result = await verifier.verify_gist("octocat/abcdef1234567890abcd", "{}", "sig")

        assert result is True

    @pytest.mark.asyncio
    async def test_failure_when_key_does_not_verify(self) -> None:
        verifier = Verifier()
        verifier.fetch_gist_pubkey = AsyncMock(return_value="ssh-ed25519 AAAA cc-sentiment")

        with patch.object(verifier, "_verify_with_ssh_key", return_value=False):
            result = await verifier.verify_gist("octocat/abcdef1234567890abcd", "{}", "sig")

        assert result is False


class TestGistKeyCache:
    @pytest.mark.asyncio
    async def test_populates_cache_on_miss(self) -> None:
        cache = DictKeyCache()
        verifier = Verifier(key_cache=cache)
        verifier.fetch_gist_pubkey = AsyncMock(return_value="ssh-ed25519 KEY1 cc-sentiment")

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            await verifier.verify_gist("octocat/abcdef1234567890abcd", "{}", "sig")

        assert cache.d["gist:abcdef1234567890abcd"] == "ssh-ed25519 KEY1 cc-sentiment"

    @pytest.mark.asyncio
    async def test_uses_cached_key(self) -> None:
        cache = DictKeyCache()
        cache.d["gist:abcdef1234567890abcd"] = "ssh-ed25519 CACHED cc-sentiment"
        verifier = Verifier(key_cache=cache)
        verifier.fetch_gist_pubkey = AsyncMock()

        with patch.object(verifier, "_verify_with_ssh_key", return_value=True):
            await verifier.verify_gist("octocat/abcdef1234567890abcd", "{}", "sig")

        verifier.fetch_gist_pubkey.assert_not_called()

    @pytest.mark.asyncio
    async def test_refetches_on_mismatch(self) -> None:
        cache = DictKeyCache()
        cache.d["gist:abcdef1234567890abcd"] = "ssh-ed25519 OLD cc-sentiment"
        verifier = Verifier(key_cache=cache)
        verifier.fetch_gist_pubkey = AsyncMock(return_value="ssh-ed25519 NEW cc-sentiment")

        def verify_key(username, key, payload, sig):
            return key == "ssh-ed25519 NEW cc-sentiment"

        with patch.object(verifier, "_verify_with_ssh_key", side_effect=verify_key):
            result = await verifier.verify_gist("octocat/abcdef1234567890abcd", "{}", "sig")

        assert result is True
        assert cache.d["gist:abcdef1234567890abcd"] == "ssh-ed25519 NEW cc-sentiment"


class TestParseGistId:
    def test_splits_username_and_gist_id(self) -> None:
        assert Verifier.parse_gist_id("octocat/abc123def456") == ("octocat", "abc123def456")

    def test_missing_slash_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid gist contributor id"):
            Verifier.parse_gist_id("octocat")

    def test_empty_username_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid gist contributor id"):
            Verifier.parse_gist_id("/abc123")

    def test_empty_gist_id_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid gist contributor id"):
            Verifier.parse_gist_id("octocat/")


class TestPubkeyKindMultiArmor:
    def test_concat_pgp_armor_blocks_recognized(self) -> None:
        concat = (
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\nblock-a\n-----END PGP PUBLIC KEY BLOCK-----\n"
            "-----BEGIN PGP PUBLIC KEY BLOCK-----\nblock-b\n-----END PGP PUBLIC KEY BLOCK-----\n"
        )
        assert Verifier.pubkey_kind(concat) == "pgp"
