from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import anyio
import pytest

from cc_sentiment.models import (
    AppState,
    ContributorId,
    ProcessedSession,
    SessionId,
    SSHConfig,
)
from cc_sentiment.upload import Uploader
from tests.helpers import make_record


class TestRecordsFromState:
    def test_returns_records_from_non_uploaded_sessions(self) -> None:
        record = make_record()
        state = AppState(
            sessions={
                SessionId("s1"): ProcessedSession(records=(record,), uploaded=False),
            },
        )
        result = Uploader.records_from_state(state)
        assert len(result) == 1
        assert result[0] == record

    def test_skips_uploaded_sessions(self) -> None:
        state = AppState(
            sessions={
                SessionId("s1"): ProcessedSession(
                    records=(make_record(),), uploaded=True
                ),
                SessionId("s2"): ProcessedSession(
                    records=(make_record(session_id="session-2"),), uploaded=False
                ),
            },
        )
        result = Uploader.records_from_state(state)
        assert len(result) == 1
        assert result[0].conversation_id == SessionId("session-2")

    def test_returns_empty_when_all_uploaded(self) -> None:
        state = AppState(
            sessions={
                SessionId("s1"): ProcessedSession(
                    records=(make_record(),), uploaded=True
                ),
            },
        )
        result = Uploader.records_from_state(state)
        assert result == []


class TestUpload:
    def test_raises_when_config_is_none(self) -> None:
        state = AppState(config=None)
        uploader = Uploader()

        async def do_upload() -> None:
            await uploader.upload([make_record()], state)

        with pytest.raises(AssertionError, match="not configured"):
            anyio.run(do_upload)

    def test_marks_sessions_uploaded_after_success(self) -> None:
        record = make_record()
        state = AppState(
            config=SSHConfig(
                contributor_id=ContributorId("testuser"),
                key_path=Path("/home/.ssh/id_ed25519"),
            ),
            sessions={
                SessionId("session-1"): ProcessedSession(
                    records=(record,), uploaded=False
                ),
            },
        )

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()

        mock_http_client = AsyncMock()
        mock_http_client.post.return_value = mock_response

        mock_ctx = MagicMock()
        mock_ctx.__aenter__ = AsyncMock(return_value=mock_http_client)
        mock_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("cc_sentiment.upload.PayloadSigner.sign_records", return_value="fake-sig"), \
             patch("cc_sentiment.upload.httpx.AsyncClient", return_value=mock_ctx), \
             patch.object(AppState, "save"):
            uploader = Uploader()

            async def do_upload() -> None:
                await uploader.upload([record], state)

            anyio.run(do_upload)

        assert state.sessions[SessionId("session-1")].uploaded is True
