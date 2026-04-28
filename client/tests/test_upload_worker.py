from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import anyio
import httpx

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from cc_sentiment.repo import Repository
from cc_sentiment.upload import UploadPool, UploadProgress, Uploader
from tests.helpers import make_record


def _make_pool(state: AppState, db_path: Path) -> UploadPool:
    return UploadPool(
        uploader=Uploader(),
        state=state,
        repo=Repository.open(db_path),
        progress=UploadProgress(),
        on_progress_change=lambda _: None,
    )


async def test_upload_worker_retries_transient_network_errors(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record()])
    send.close()

    calls = 0

    async def fake_upload(self, batch, state, repo, on_progress=None):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("boom")

    with patch("cc_sentiment.upload.Uploader.upload", fake_upload), \
         patch("cc_sentiment.upload.anyio.sleep", new_callable=AsyncMock):
        await pool._worker_loop(recv, worker_id=0)

    assert calls == 2
    assert pool.progress.uploaded_records == 1
    assert pool.progress.failed_batches == 0
    assert pool.progress.fatal is None


async def test_upload_worker_records_partial_failure_after_retries_exhaust(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record(session_id="s1")])
    send.send_nowait([make_record(session_id="s2")])
    send.close()

    async def always_fail(self, batch, state, repo, on_progress=None):
        raise httpx.ConnectError("down")

    with patch("cc_sentiment.upload.Uploader.upload", always_fail), \
         patch("cc_sentiment.upload.anyio.sleep", new_callable=AsyncMock):
        await pool._worker_loop(recv, worker_id=0)

    assert pool.progress.failed_batches == 2
    assert pool.progress.uploaded_records == 0
    assert pool.progress.fatal is None


async def test_upload_worker_fatal_on_401_drops_subsequent_batches(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("u"), key_path=Path("/k")))
    pool = _make_pool(state, tmp_path / "records.db")

    send, recv = anyio.create_memory_object_stream[list](float("inf"))
    send.send_nowait([make_record(session_id="s1")])
    send.send_nowait([make_record(session_id="s2")])
    send.close()

    calls = 0

    async def reject_first(self, batch, state, repo, on_progress=None):
        nonlocal calls
        calls += 1
        raise httpx.HTTPStatusError(
            "nope",
            request=httpx.Request("POST", "http://x"),
            response=httpx.Response(401),
        )

    with patch("cc_sentiment.upload.Uploader.upload", reject_first):
        await pool._worker_loop(recv, worker_id=0)

    assert calls == 1
    assert isinstance(pool.progress.fatal, httpx.HTTPStatusError)
    assert pool.progress.fatal.response.status_code == 401
    assert pool.progress.uploaded_records == 0
    assert pool.progress.failed_batches == 0
