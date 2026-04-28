from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from cc_sentiment.models import AppState, ContributorId, SSHConfig
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.upload import AuthServerError, AuthUnreachable
from tests.helpers import make_scan


async def test_authenticate_returns_true_when_creds_valid(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is True


async def test_authenticate_returns_false_on_unreachable(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnreachable(detail="connect refused"),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is False


async def test_authenticate_returns_false_on_server_error(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthServerError(status=500),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is False


async def test_run_flow_aborts_when_authenticate_returns_false(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_run), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnreachable(detail="no net"),
         ):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            mock_run.assert_not_called()
