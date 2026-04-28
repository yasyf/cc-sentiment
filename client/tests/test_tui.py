
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import httpx
import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, Static

from cc_sentiment.models import (
    AppState,
    ContributorId,
    GistConfig,
    GPGConfig,
    MyStat,
    SSHConfig,
)
from cc_sentiment.repo import Repository
from cc_sentiment.engines import (
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeStatus,
    ClaudeUnavailable,
)
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.tui.moments_view import MomentsView
from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.screens import (
    CostReviewScreen,
    PlatformErrorScreen,
    StatShareScreen,
)
from cc_sentiment.tui.stages import (
    Discovering,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)
from cc_sentiment.tui.widgets import (
    CommandBox,
    HourlyChart,
)
from cc_sentiment.upload import (
    DASHBOARD_URL,
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    UploadPool,
    UploadProgress,
    Uploader,
)
from tests.helpers import make_record, make_scan


@pytest.fixture
def auth_ok():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        yield


@pytest.fixture
def auth_unauthorized():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthUnauthorized(status=401),
    ):
        yield


@pytest.fixture
def no_stat_share():
    async def _noop(self, config, push_share):
        return None
    with patch.object(CCSentimentApp, "_fetch_card", new=_noop):
        yield


class CostHarness(App[None]):
    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(CostReviewScreen(self.bucket_count, self.model), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


async def test_cost_review_renders_bucket_count_and_cost():
    harness = CostHarness(500, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        text = " ".join(
            str(w.render()) for w in pilot.app.screen.query("Label, Static")
        )
        assert "500" in text
        assert "Claude" in text


async def test_cost_review_continue_dismisses_true():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-yes")
        await pilot.pause()
        assert harness.dismissed is True


async def test_cost_review_cancel_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-no")
        await pilot.pause()
        assert harness.dismissed is False


async def test_cost_review_escape_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert harness.dismissed is False


class ErrorHarness(App[None]):
    def __init__(self, status: ClaudeStatus) -> None:
        super().__init__()
        self.status = status
        self.dismissed: object = "not-yet"

    def on_mount(self) -> None:
        self.push_screen(PlatformErrorScreen(self.status), self._capture)

    def _capture(self, result: object) -> None:
        self.dismissed = result


async def test_platform_error_not_installed_shows_brew_install():
    harness = ErrorHarness(ClaudeNotInstalled(brew_available=True))
    async with harness.run_test() as pilot:
        await pilot.pause()
        boxes = pilot.app.screen.query(CommandBox)
        commands = [b.command for b in boxes]
        assert "brew install --cask claude-code" in commands
        assert "claude auth login" in commands


async def test_platform_error_not_installed_without_brew_shows_curl():
    harness = ErrorHarness(ClaudeNotInstalled(brew_available=False))
    async with harness.run_test() as pilot:
        await pilot.pause()
        commands = [b.command for b in pilot.app.screen.query(CommandBox)]
        assert any("install.sh" in c for c in commands)
        assert "claude auth login" in commands


async def test_platform_error_not_authenticated_shows_auth_only():
    harness = ErrorHarness(ClaudeNotAuthenticated())
    async with harness.run_test() as pilot:
        await pilot.pause()
        commands = [b.command for b in pilot.app.screen.query(CommandBox)]
        assert commands == ["claude auth login"]


async def test_platform_error_quit_dismisses():
    harness = ErrorHarness(ClaudeNotAuthenticated())
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit-btn")
        await pilot.pause()
        assert harness.dismissed is None


async def test_ccsentiment_app_engine_failure_shows_error_and_exits(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.app.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_debug_mode_composes(tmp_path: Path):
    from cc_sentiment.tui.widgets.debug_section import DebugSection

    state = AppState()
    db_path = tmp_path / "records.db"
    with patch(
        "cc_sentiment.tui.app.EngineFactory.resolve",
        side_effect=ClaudeUnavailable(ClaudeNotInstalled(brew_available=True)),
    ), patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path, debug=True)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.1)
            assert pilot.app.query_one(DebugSection) is not None


async def test_ccsentiment_app_claude_engine_shows_cost_review(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 50))):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            assert pilot.app.screen.bucket_count == 50


async def test_ccsentiment_app_cost_cancel_exits(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_pipeline_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 50))), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_pipeline_run):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            await pilot.click("#cost-no")
            await pilot.pause()
            mock_pipeline_run.assert_not_called()


async def test_ccsentiment_app_idle_when_no_work(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, (IdleEmpty, IdleCaughtUp))
            assert "all" in app.status_text.lower() or "set" in app.status_text.lower()


async def test_ccsentiment_app_rescan_clears_state(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, IdleAfterUpload)

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.stage, RescanConfirm)

            await pilot.press("r")
            await pilot.pause()

    verify = Repository.open(db_path)
    try:
        assert verify.stats() == (0, 0, 0)
    finally:
        verify.close()


async def test_ccsentiment_app_runs_pipeline_and_uploads(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    mock_upload = AsyncMock()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", mock_upload):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

            mock_upload.assert_awaited_once()


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


async def test_authenticate_unauthorized_clears_config_and_pushes_setup(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def user_cancels_setup(screen) -> bool:
        return False

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch(
             "cc_sentiment.upload.Uploader.probe_credentials",
             new_callable=AsyncMock,
             return_value=AuthUnauthorized(status=401),
         ), \
         patch.object(CCSentimentApp, "push_screen_wait", side_effect=user_cancels_setup) as mock_push:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            result = await app._authenticate()
            assert result is False
            assert app.state.config is None
            mock_push.assert_awaited()


async def test_auto_open_dashboard_opens_url_after_delay(tmp_path: Path, auth_ok, monkeypatch):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    monkeypatch.setattr(CCSentimentApp, "AUTO_OPEN_DASHBOARD_DELAY_SECONDS", 0.0)

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.tui.app.webbrowser.open") as mock_open:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._auto_open_dashboard()
            mock_open.assert_called_once_with(DASHBOARD_URL)


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


def test_format_duration_under_30_seconds():
    assert TimeFormat.format_duration(0) == "a few seconds"
    assert TimeFormat.format_duration(29) == "a few seconds"


def test_format_duration_minutes():
    assert TimeFormat.format_duration(60) == "~1 min"
    assert TimeFormat.format_duration(900) == "~15 min"


def test_format_duration_hours():
    assert TimeFormat.format_duration(3600) == "~1 hour"
    assert TimeFormat.format_duration(7200) == "~2 hours"


def test_format_hour_short_matches_dashboard():
    assert TimeFormat.format_hour_short(0) == "12a"
    assert TimeFormat.format_hour_short(5) == "5a"
    assert TimeFormat.format_hour_short(12) == "12p"
    assert TimeFormat.format_hour_short(17) == "5p"
    assert TimeFormat.format_hour_short(23) == "11p"


def test_score_emoji_for_score_and_avg():
    from cc_sentiment.tui.format import ScoreEmoji

    assert ScoreEmoji.for_score(1) == "😡"
    assert ScoreEmoji.for_score(3) == "😐"
    assert ScoreEmoji.for_score(5) == "🤩"
    assert ScoreEmoji.for_avg(2.4) == "😒"
    assert ScoreEmoji.for_avg(4.6) == "🤩"


async def test_set_total_renders_eta_when_hardware_estimates(tmp_path: Path, auth_ok):
    from textual.widgets import Label
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=10.0):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(1200, "mlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:02:00" in label_text
            assert app.status_text == ""


async def test_set_total_omits_eta_when_hardware_unknown(tmp_path: Path, auth_ok):
    from textual.widgets import Label
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(500, "mlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:00:00" in label_text
            assert app.status_text == ""


async def test_add_buckets_updates_progress(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(100, "mlx", 0)
            app._add_buckets(5)
            app._add_buckets(3)
            assert app.scored == 8


async def test_action_open_dashboard_opens_browser(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.tui.app.webbrowser.open") as mock_open:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("o")
            await pilot.pause()
            mock_open.assert_called_once_with(DASHBOARD_URL)
            assert DASHBOARD_URL in app.status_text


async def test_enter_idle_after_upload_mentions_dashboard(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)

            await app._enter_idle(uploaded=True)
            assert isinstance(app.stage, IdleAfterUpload)
            assert "Uploaded" in app.status_text
            assert "sentiments.cc" in app.status_text

            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleCaughtUp)
            assert "Uploaded" not in app.status_text
            assert "O to open aggregate stats" in app.status_text


async def test_enter_idle_empty_state_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleEmpty)
            assert "No conversations yet" in app.status_text
            assert "O to open aggregate stats" in app.status_text


async def test_successful_upload_lands_in_idle_after_upload(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 2))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)
            assert isinstance(app.stage, IdleAfterUpload)
            assert "sentiments.cc" in app.status_text


async def test_stage_transitions_across_successful_run(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3)]
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    seen: list[type[Stage]] = []

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        original_watch = app.watch_stage

        def recording_watch(stage: Stage) -> None:
            seen.append(type(stage))
            original_watch(stage)

        app.watch_stage = recording_watch  # type: ignore[method-assign]

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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, IdleAfterUpload)
            prev = app.stage

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.stage, RescanConfirm)
            assert app.stage.prev == prev

            await app._cancel_rescan()
            assert app.stage == prev


def _make_pool(state: AppState, db_path: Path) -> UploadPool:
    return UploadPool(
        uploader=Uploader(),
        state=state,
        repo=Repository.open(db_path),
        progress=UploadProgress(),
        on_progress_change=lambda _: None,
    )


async def test_upload_worker_retries_transient_network_errors(tmp_path: Path):
    import anyio

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
    import anyio

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
    import anyio

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


class ChartHarness(App[None]):
    def compose(self):
        yield HourlyChart(id="chart")


async def test_hourly_chart_renders_volume_row_and_axis():
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=5, time=base + timedelta(hours=8)),
        make_record(score=1, time=base + timedelta(hours=14)),
        make_record(score=3, time=base + timedelta(hours=20)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        lines = str(chart.content).split("\n")
        assert len(lines) == 5
        bar_row = lines[2]
        assert "[$success]" in bar_row
        assert "[$error]" in bar_row
        assert "[$warning]" in bar_row
        assert "─" * 24 in lines[3]
        labels = lines[4]
        assert "12a" in labels
        assert "6a" in labels
        assert "12p" in labels
        assert "6p" in labels


async def test_hourly_chart_colors_track_avg_sentiment():
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=1, time=base + timedelta(hours=8)),
        make_record(score=2, time=base + timedelta(hours=8, minutes=1)),
        make_record(score=1, time=base + timedelta(hours=9)),
        make_record(score=4, time=base + timedelta(hours=10)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        bar_row = str(chart.content).split("\n")[2]
        assert "[$error]" in bar_row
        assert "[$success]" in bar_row


async def test_hourly_chart_drops_records_older_than_window():
    from datetime import datetime, timedelta, timezone

    old = datetime.now(timezone.utc) - timedelta(days=HourlyChart.WINDOW_DAYS + 1)
    records = [
        make_record(score=1, time=old),
        make_record(score=1, time=old + timedelta(hours=1)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        assert "no data yet" in str(chart.content)


async def test_hourly_chart_empty_records():
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart([])
        await pilot.pause()
        assert "no data yet" in str(chart.content)


async def test_hourly_chart_caption_calls_out_tough_hour():
    from datetime import datetime, timedelta, timezone

    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=1, time=base + timedelta(hours=17, minutes=i))
        for i in range(HourlyChart.UCB_MIN_SAMPLES)
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        caption = str(chart.content).split("\n")[0]
        assert "tough hour" in caption


class SentimentPanelHarness(App[None]):
    def compose(self):
        from cc_sentiment.tui.widgets import SentimentPanel
        yield SentimentPanel(id="panel")


async def test_sentiment_panel_empty_state():
    from cc_sentiment.tui.widgets import SentimentPanel
    async with SentimentPanelHarness().run_test() as pilot:
        panel = pilot.app.query_one("#panel", SentimentPanel)
        panel.update_from_records([])
        await pilot.pause()
        assert "warming up" in str(panel.content)


async def test_sentiment_panel_renders_histogram():
    from cc_sentiment.tui.widgets import SentimentPanel
    records = [
        make_record(score=1), make_record(score=1), make_record(score=2),
        make_record(score=3), make_record(score=3), make_record(score=3),
        make_record(score=4), make_record(score=4), make_record(score=5),
    ]
    async with SentimentPanelHarness().run_test() as pilot:
        panel = pilot.app.query_one("#panel", SentimentPanel)
        panel.update_from_records(records)
        await pilot.pause()
        body = str(panel.content)
        assert "frustrated" in body
        assert "chats" in body
        assert "😡" in body
        assert "🤩" in body
        assert "[$success]" in body
        assert "[$error]" in body


class MomentsHarness(App[None]):
    def compose(self):
        with Vertical(id="section"):
            yield Static("", id="log")


async def test_moments_view_snippet_survives_bracket_heavy_content():
    with patch("cc_sentiment.tui.moments_view.random.random", return_value=0.0):
        async with MomentsHarness().run_test() as pilot:
            moments = MomentsView(
                app=pilot.app,
                section=pilot.app.query_one("#section"),
                log=pilot.app.query_one("#log", Static),
            )
            moments.show()
            await moments.add_snippet(
                "2026-04-03T11:14:13.287367+0000 +13m26s [🐞][DSPyCompilationServer.compile] 'ignore'",
                1,
            )
            moments.last_snippet_at = 0.0
            await moments.add_snippet("prefix text [dim", 1)
            moments.last_snippet_at = 0.0
            await moments.add_snippet("<task-notification> <task-id>abc</task-id> body", 5)
            await pilot.pause()
            assert len(moments.lines) >= 1


STAT = MyStat(
    kind="kindness",
    percentile=72,
    text="nicer to Claude than 72% of developers",
    tweet_text="I'm nicer to Claude than 72% of developers.",
    total_contributors=100,
)

GITHUB_CONFIG = SSHConfig(
    contributor_id=ContributorId("testuser"),
    key_path=Path("/home/.ssh/id_ed25519"),
)
GPG_CONFIG = GPGConfig(
    contributor_type="gpg",
    contributor_id=ContributorId("gpg-user-id"),
    fpr="ABCDEF0123456789",
)


class StatShareHarness(App[None]):
    def __init__(self, config: SSHConfig | GPGConfig | GistConfig, stat: MyStat) -> None:
        super().__init__()
        self.config = config
        self.stat = stat

    def on_mount(self) -> None:
        self.push_screen(StatShareScreen(self.config, self.stat))


def stub_mint_share(share_id: str = "sh-abc123") -> AsyncMock:
    from cc_sentiment.models import ShareMintResponse
    return AsyncMock(return_value=ShareMintResponse(
        id=share_id,
        url=f"https://sentiments.cc/share/{share_id}",
    ))


async def test_stat_share_renders_stat_text():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            text = " ".join(
                str(w.render()) for w in pilot.app.screen.query("Label, Static")
            )
            assert "nicer to Claude than 72% of developers" in text


async def test_stat_share_tweet_button_opens_share_url():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share("sh-xyz789")), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-tweet")
            await pilot.pause()

    mock_open.assert_called_once()
    url = mock_open.call_args[0][0]
    assert "twitter.com/intent/tweet" in url
    assert "share%2Fsh-xyz789" in url or "share/sh-xyz789" in url
    assert "nicer+to+Claude" in url or "nicer%20to%20Claude" in url


async def test_stat_share_tweet_button_disabled_until_mint_resolves():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    mint_event = __import__("anyio").Event()

    async def slow_mint(self, config):
        await mint_event.wait()
        from cc_sentiment.models import ShareMintResponse
        return ShareMintResponse(id="sh-late", url="https://sentiments.cc/share/sh-late")

    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=slow_mint), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.1)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert tweet.disabled is True
            await pilot.click("#stat-tweet")
            await pilot.pause()
            assert not mock_open.called

            mint_event.set()
            await pilot.pause(delay=0.3)
            assert tweet.disabled is False


async def test_stat_share_surfaces_signing_failure_on_label():
    import subprocess

    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    failing_mint = AsyncMock(
        side_effect=subprocess.CalledProcessError(1, ["ssh-keygen"])
    )
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=failing_mint):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert str(tweet.label) == "Share unavailable"
            assert tweet.disabled is True


async def test_stat_share_surfaces_signing_timeout_on_label():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    failing_mint = AsyncMock(side_effect=TimeoutError())
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=failing_mint):
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            tweet = pilot.app.screen.query_one("#stat-tweet", Button)
            assert str(tweet.label) == "Share unavailable"
            assert tweet.disabled is True


async def test_stat_share_skip_dismisses_without_opening_browser():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.click("#stat-skip")
            await pilot.pause()

    mock_open.assert_not_called()


async def test_stat_share_escape_dismisses():
    harness = StatShareHarness(GITHUB_CONFIG, STAT)
    with patch("cc_sentiment.tui.screens.stat_share.Uploader.mint_share", new=stub_mint_share()), \
         patch("cc_sentiment.tui.screens.stat_share.webbrowser.open") as mock_open:
        async with harness.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("escape")
            await pilot.pause()

    mock_open.assert_not_called()


async def test_cta_shows_schedule_when_daemon_not_installed(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.view.cta.schedule_available is True
            assert app.view.cta.showing == "schedule"
            section = pilot.app.query_one("#cta-section")
            assert "inactive" not in section.classes
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Run daily"


async def test_cta_hides_when_daemon_installed_and_no_tweet(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=True), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.view.cta.schedule_available is False
            assert app.view.cta.has_tweet() is False
            section = pilot.app.query_one("#cta-section")
            assert "inactive" in section.classes


async def test_cta_rotates_between_tweet_and_schedule(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.view.cta.showing == "schedule"
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Run daily"

            app.view.rotate_cta()
            await pilot.pause()
            assert app.view.cta.showing == "tweet"
            assert str(button.label) == "Share on X"
            title = pilot.app.query_one("#cta-title", Static)
            assert "nicer to Claude" in str(title.render())

            app.view.rotate_cta()
            await pilot.pause()
            assert app.view.cta.showing == "schedule"


async def test_cta_pins_to_tweet_after_install_succeeds(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=False), \
         patch("cc_sentiment.tui.app.LaunchAgent.install") as mock_install, \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="mlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.view.cta.showing == "schedule"

            await pilot.click("#cta-action")
            await pilot.pause(delay=0.2)

            mock_install.assert_called_once()
            assert app.view.cta.schedule_available is False
            assert app.view.cta.showing == "tweet"


async def test_card_fetcher_invokes_on_ready_when_stat_arrives():
    from cc_sentiment.tui.screens.stat_share import CardFetcher

    calls: list[MyStat] = []
    states: list[tuple[str, float, str]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=STAT,
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == [STAT]
    assert any(state[2] == "ready" for state in states)


async def test_card_fetcher_reports_no_card_on_404():
    from cc_sentiment.tui.screens.stat_share import CardFetcher

    calls: list[MyStat] = []
    states: list[tuple[str, float, str]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == []
    assert states[-1] == ("http 404", states[-1][1], "no card")


async def test_card_fetcher_reports_error_on_network_failure():
    from cc_sentiment.tui.screens.stat_share import CardFetcher

    calls: list[MyStat] = []
    states: list[tuple[str, float, str]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("no net"),
    ):
        await CardFetcher(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda s, e, stop: states.append((s, e, stop)),
        ).run()

    assert calls == []
    assert any(state[2] == "error" for state in states)
