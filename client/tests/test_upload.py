from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from cc_sentiment.models import (
    AppState,
    ContributorId,
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
