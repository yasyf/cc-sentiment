from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from cc_sentiment.engines import ClaudeNotInstalled, ClaudeUnavailable
from cc_sentiment.models import AppState, ContributorId, GPGConfig, SSHConfig
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.tui.popovers import PlatformErrorScreen
from cc_sentiment.tui.dashboard.stages import IdleCaughtUp, IdleEmpty
from cc_sentiment.tui.dashboard.widgets.debug_section import DebugSection
from tests.helpers import make_record, make_scan


async def test_ccsentiment_app_engine_failure_shows_error_and_exits(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.dashboard.lifecycle.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_debug_mode_composes(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.dashboard.lifecycle.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path, debug=True)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.1)
            assert pilot.app.dashboard.query_one(DebugSection) is not None


async def test_ccsentiment_app_idle_when_no_work(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.dashboard.lifecycle.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.dashboard.stage, (IdleEmpty, IdleCaughtUp))
            assert "all" in app.dashboard.status_text.lower() or "set" in app.dashboard.status_text.lower()


async def test_ccsentiment_app_runs_pipeline_and_uploads(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    mock_upload = AsyncMock()

    with patch("cc_sentiment.tui.dashboard.lifecycle.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", mock_upload):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

            mock_upload.assert_awaited_once()
