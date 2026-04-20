from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistConfig,
    GPGConfig,
    SessionId,
    SSHConfig,
)
from cc_sentiment.repo import Repository
from cc_sentiment.upload import (
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    Uploader,
)
from tests.helpers import make_record


import httpx


@pytest.fixture
def repo(tmp_path: Path) -> Iterator[Repository]:
    r = Repository.open(tmp_path / "records.db")
    try:
        yield r
    finally:
        r.close()


class TestPendingRecords:
    def test_returns_records_from_non_uploaded_sessions(self, repo: Repository) -> None:
        record = make_record()
        repo.save_records("/p.jsonl", 1.0, [record])

        result = repo.pending_records()
        assert len(result) == 1
        assert result[0] == record

    def test_skips_uploaded_sessions(self, repo: Repository) -> None:
        uploaded = make_record(session_id="s1")
        pending = make_record(session_id="session-2")
        repo.save_records("/p.jsonl", 1.0, [uploaded, pending])
        repo.mark_sessions_uploaded({SessionId("s1")})

        result = repo.pending_records()
        assert len(result) == 1
        assert result[0].conversation_id == SessionId("session-2")

    def test_returns_empty_when_all_uploaded(self, repo: Repository) -> None:
        record = make_record()
        repo.save_records("/p.jsonl", 1.0, [record])
        repo.mark_sessions_uploaded({record.conversation_id})

        assert repo.pending_records() == []


class TestUpload:
    def test_raises_when_config_is_none(self, repo: Repository) -> None:
        state = AppState(config=None)
        uploader = Uploader()

        async def do_upload() -> None:
            await uploader.upload([make_record()], state, repo)

        with pytest.raises(AssertionError, match="not configured"):
            anyio.run(do_upload)

    def test_marks_sessions_uploaded_after_success(self, repo: Repository) -> None:
        record = make_record()
        repo.save_records("/p.jsonl", 1.0, [record])

        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=Path("/home/.ssh/id_ed25519"),
            ),
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment.upload.PayloadSigner.sign_records", return_value="fake-sig"), \
             patch("cc_sentiment.upload.httpx.AsyncClient", return_value=mock_ctx):
            uploader = Uploader()

            async def do_upload() -> None:
                await uploader.upload([record], state, repo)

            anyio.run(do_upload)

        assert repo.pending_records() == []


class TestProbeCredentials:
    def test_returns_ok_on_2xx(self) -> None:
        config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        uploader = Uploader()

        async def run() -> object:
            with patch.object(Uploader, "_verify_credentials", new=AsyncMock()):
                return await uploader.probe_credentials(config)

        assert isinstance(anyio.run(run), AuthOk)

    @pytest.mark.parametrize("status", [401, 403])
    def test_returns_unauthorized_on_401_403(self, status: int) -> None:
        config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        uploader = Uploader()
        response = MagicMock()
        response.status_code = status
        error = httpx.HTTPStatusError("denied", request=MagicMock(), response=response)

        async def run() -> object:
            with patch.object(Uploader, "_verify_credentials", new=AsyncMock(side_effect=error)):
                return await uploader.probe_credentials(config)

        result = anyio.run(run)
        assert isinstance(result, AuthUnauthorized)
        assert result.status == status

    def test_returns_server_error_on_500(self) -> None:
        config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        uploader = Uploader()
        response = MagicMock()
        response.status_code = 500
        error = httpx.HTTPStatusError("boom", request=MagicMock(), response=response)

        async def run() -> object:
            with patch.object(Uploader, "_verify_credentials", new=AsyncMock(side_effect=error)):
                return await uploader.probe_credentials(config)

        result = anyio.run(run)
        assert isinstance(result, AuthServerError)
        assert result.status == 500

    def test_returns_unreachable_on_connect_error(self) -> None:
        config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        uploader = Uploader()

        async def run() -> object:
            err = httpx.ConnectError("refused")
            with patch.object(Uploader, "_verify_credentials", new=AsyncMock(side_effect=err)):
                return await uploader.probe_credentials(config)

        result = anyio.run(run)
        assert isinstance(result, AuthUnreachable)
        assert "refused" in result.detail

    def test_returns_unreachable_on_timeout(self) -> None:
        config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        uploader = Uploader()

        async def run() -> object:
            err = httpx.TimeoutException("slow")
            with patch.object(Uploader, "_verify_credentials", new=AsyncMock(side_effect=err)):
                return await uploader.probe_credentials(config)

        assert isinstance(anyio.run(run), AuthUnreachable)


class TestShareUrlHelpers:
    def test_share_url(self) -> None:
        assert Uploader.share_url("abc123") == "https://sentiments.cc/share/abc123"

    def test_og_url(self) -> None:
        assert Uploader.og_url("abc123") == "https://sentiments.cc/share/abc123/og"

    def test_tweet_url_includes_share_url_and_tweet_text(self) -> None:
        url = Uploader.tweet_url("abc123", "I'm nicer to Claude than most developers.")
        assert "twitter.com/intent/tweet" in url
        assert "share%2Fabc123" in url or "share/abc123" in url
        assert "nicer" in url


class TestMintShare:
    def test_signs_payload_and_parses_response(self) -> None:
        config = SSHConfig(
            contributor_id=ContributorId("testuser"),
            key_path=Path("/home/.ssh/id_ed25519"),
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"id":"sh-abc123","url":"https://sentiments.cc/share/sh-abc123"}'

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment.upload.PayloadSigner.sign", return_value="fake-sig") as sign, \
             patch("cc_sentiment.upload.httpx.AsyncClient", return_value=mock_ctx):
            uploader = Uploader()

            async def do_mint() -> object:
                return await uploader.mint_share(config)

            result = anyio.run(do_mint)

        assert result.id == "sh-abc123"
        assert result.url == "https://sentiments.cc/share/sh-abc123"

        sign.assert_called_once()
        canonical = sign.call_args[0][0]
        assert '"issued_at":' in canonical
        assert canonical.startswith('{"issued_at":')

        mock_http_client.post.assert_called_once()
        posted = mock_http_client.post.call_args
        body = posted.kwargs["content"].decode()
        assert '"contributor_type":"github"' in body
        assert '"contributor_id":"testuser"' in body
        assert '"signature":"fake-sig"' in body
        assert '"issued_at":' in body

    def test_gist_wires_packed_contributor_id(self) -> None:
        config = GistConfig(
            contributor_id=ContributorId("octocat"),
            key_path=Path("/tmp/id_ed25519"),
            gist_id="abcdef1234567890abcd",
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.text = '{"id":"sh-x","url":"https://sentiments.cc/share/sh-x"}'

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment.upload.PayloadSigner.sign", return_value="sig"), \
             patch("cc_sentiment.upload.httpx.AsyncClient", return_value=mock_ctx):
            uploader = Uploader()

            async def do_mint() -> object:
                return await uploader.mint_share(config)

            anyio.run(do_mint)

        body = mock_http_client.post.call_args.kwargs["content"].decode()
        assert '"contributor_id":"octocat/abcdef1234567890abcd"' in body


class TestWireContributorId:
    def test_gist_packs_username_and_gist_id(self) -> None:
        config = GistConfig(
            contributor_id=ContributorId("octocat"),
            key_path=Path("/tmp/id_ed25519"),
            gist_id="abcdef1234567890abcd",
        )
        assert Uploader.wire_contributor_id(config) == "octocat/abcdef1234567890abcd"

    def test_ssh_returns_plain_contributor_id(self) -> None:
        config = SSHConfig(
            contributor_id=ContributorId("octocat"),
            key_path=Path("/home/.ssh/id_ed25519"),
        )
        assert Uploader.wire_contributor_id(config) == "octocat"

    def test_gpg_returns_plain_contributor_id(self) -> None:
        config = GPGConfig(
            contributor_type="github",
            contributor_id=ContributorId("octocat"),
            fpr="ABCDEF1234567890",
        )
        assert Uploader.wire_contributor_id(config) == "octocat"
