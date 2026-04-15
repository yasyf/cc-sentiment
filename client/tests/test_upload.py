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
from cc_sentiment.upload import Uploader
from tests.helpers import make_record


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
