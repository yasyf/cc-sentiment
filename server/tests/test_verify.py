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
            await Verifier().verify_signature("unknown", "user", "{}", "sig")

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
