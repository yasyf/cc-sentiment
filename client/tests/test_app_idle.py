from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

from cc_sentiment.models import AppState, ContributorId, GPGConfig, SSHConfig
from cc_sentiment.repo import Repository
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.tui.dashboard import DashboardScreen
from cc_sentiment.tui.dashboard.stages import (
    Discovering,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)
from cc_sentiment.upload import DASHBOARD_URL
from tests.helpers import make_record, make_scan


async def test_auto_open_dashboard_opens_url_after_delay(tmp_path: Path, auth_ok, monkeypatch):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    monkeypatch.setattr(DashboardScreen, "AUTO_OPEN_DASHBOARD_DELAY_SECONDS", 0.0)

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app.dashboard._auto_open_dashboard()
            app.open_url.assert_called_once_with(DASHBOARD_URL)


async def test_action_open_dashboard_opens_browser(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("o")
            await pilot.pause()
            app.open_url.assert_called_once_with(DASHBOARD_URL)
            assert DASHBOARD_URL in app.dashboard.status_text


async def test_enter_idle_after_upload_mentions_dashboard(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)

            await app.dashboard._enter_idle(uploaded=True)
            assert isinstance(app.dashboard.stage, IdleAfterUpload)
            assert "Uploaded" in app.dashboard.status_text
            assert "sentiments.cc" in app.dashboard.status_text

            await app.dashboard._enter_idle(uploaded=False)
            assert isinstance(app.dashboard.stage, IdleCaughtUp)
            assert "Uploaded" not in app.dashboard.status_text
            assert "O to open aggregate stats" in app.dashboard.status_text


async def test_enter_idle_empty_state_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app.dashboard._enter_idle(uploaded=False)
            assert isinstance(app.dashboard.stage, IdleEmpty)
            assert "No conversations yet" in app.dashboard.status_text
            assert "O to open aggregate stats" in app.dashboard.status_text


async def test_successful_upload_lands_in_idle_after_upload(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)
            assert isinstance(app.dashboard.stage, IdleAfterUpload)
            assert "sentiments.cc" in app.dashboard.status_text


async def test_stage_transitions_across_successful_run(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3)]
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    seen: list[type[Stage]] = []

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        original_watch = app.dashboard.watch_stage

        def recording_watch(stage: Stage) -> None:
            seen.append(type(stage))
            original_watch(stage)

        app.dashboard.watch_stage = recording_watch  # type: ignore[method-assign]

        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

    assert Discovering in seen
    assert Scoring in seen
    assert Uploading in seen
    assert IdleAfterUpload in seen
    assert seen.index(Discovering) < seen.index(Scoring) < seen.index(Uploading) < seen.index(IdleAfterUpload)


async def test_rescan_confirm_restores_previous_stage_on_cancel(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.dashboard.stage, IdleAfterUpload)
            prev = app.dashboard.stage

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.dashboard.stage, RescanConfirm)
            assert app.dashboard.stage.prev == prev

            await app.dashboard._cancel_rescan()
            assert app.dashboard.stage == prev


async def test_ccsentiment_app_rescan_clears_state(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.dashboard.flow.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.dashboard.stage, IdleAfterUpload)

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.dashboard.stage, RescanConfirm)

            await pilot.press("r")
            await pilot.pause()

    verify = Repository.open(db_path)
    try:
        assert verify.stats() == (0, 0, 0)
    finally:
        verify.close()
