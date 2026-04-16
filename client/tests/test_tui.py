from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from textual.app import App
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label

from cc_sentiment.models import AppState, ContributorId, GPGConfig, SSHConfig
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui import (
    DASHBOARD_URL,
    CCSentimentApp,
    CostReviewScreen,
    Discovering,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    PlatformErrorScreen,
    RescanConfirm,
    Scoring,
    SetupScreen,
    Stage,
    Uploading,
    format_duration,
)
from cc_sentiment.upload import (
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
)
from tests.helpers import make_record


class SetupHarness(App[None]):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self.state = state
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(SetupScreen(self.state), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


@pytest.fixture
def no_auto_setup():
    with patch("cc_sentiment.tui.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.detect_git_username", return_value=None), \
         patch("cc_sentiment.upload.Uploader.probe_credentials", new_callable=AsyncMock, return_value=AuthOk()):
        yield


@pytest.fixture
def auth_ok():
    with patch(
        "cc_sentiment.upload.Uploader.probe_credentials",
        new_callable=AsyncMock,
        return_value=AuthOk(),
    ):
        yield


async def test_setup_mounts_all_steps(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        for step in ("step-loading", "step-username", "step-discovery", "step-remote", "step-upload", "step-done"):
            assert pilot.app.screen.query_one(f"#{step}") is not None


async def test_setup_starts_on_loading_then_falls_to_username(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        assert pilot.app.screen.query_one(ContentSwitcher).current == "step-username"


async def test_setup_empty_username_blocked(no_auto_setup):
    async with SetupHarness(AppState()).run_test() as pilot:
        await pilot.pause(delay=0.3)
        pilot.app.screen.query_one("#username-input", Input).value = ""
        await pilot.click("#username-next")
        await pilot.pause()
        assert pilot.app.screen.query_one(ContentSwitcher).current == "step-username"


async def test_setup_auto_detect_prepopulates_username():
    with patch("cc_sentiment.tui.AutoSetup.run", new_callable=AsyncMock, return_value=(False, "testuser")), \
         patch("cc_sentiment.tui.detect_git_username", return_value="testuser"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one("#username-input", Input).value == "testuser"


async def test_setup_auto_success_jumps_to_done():
    state = AppState()

    async def fake_run(self) -> tuple[bool, str | None]:
        self.state.config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        return True, "testuser"

    with patch("cc_sentiment.tui.AutoSetup.run", new=fake_run), \
         patch.object(AppState, "save"):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one(ContentSwitcher).current == "step-done"


async def test_setup_key_discovery_populates_table(no_auto_setup):
    ssh_keys = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host"),)

    with patch("cc_sentiment.tui.KeyDiscovery.find_ssh_keys", return_value=ssh_keys), \
         patch("cc_sentiment.tui.KeyDiscovery.find_gpg_keys", return_value=()):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.query_one("#key-table", DataTable).row_count == 1
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_setup_no_keys_without_gpg_disables_next(no_auto_setup):
    with patch("cc_sentiment.tui.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.KeyDiscovery.has_tool", return_value=False):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.query_one("#key-table", DataTable).row_count == 0
            assert getattr(screen, "_generate_gpg", None) is False


async def test_setup_remote_check_ssh_found(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",)), \
         patch("cc_sentiment.tui.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.check_remotes()
            await pilot.pause(delay=0.5)

            assert screen._key_on_remote is True
            assert screen.query_one("#remote-next", Button).disabled is False


async def test_setup_remote_check_ssh_not_found(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 BBBB other",)), \
         patch("cc_sentiment.tui.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen.query_one(ContentSwitcher).current = "step-remote"
            screen.check_remotes()
            await pilot.pause(delay=0.5)

            assert screen._key_on_remote is False


async def test_setup_save_ssh_config(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch.object(AppState, "state_path", return_value=state_file):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause()

            assert isinstance(state.config, SSHConfig)
            assert state.config.contributor_id == "testuser"
            assert state.config.key_path == Path("/home/.ssh/id_ed25519")


async def test_setup_save_gpg_config(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    gpg_key = GPGKeyInfo(fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A", email="test@example.com", algo="rsa4096")

    with patch.object(AppState, "state_path", return_value=state_file):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen._save_and_finish()
            await pilot.pause()

            assert isinstance(state.config, GPGConfig)
            assert state.config.fpr == "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"


async def test_setup_done_button_dismisses_true(tmp_path: Path, no_auto_setup):
    state = AppState()
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch.object(AppState, "state_path", return_value=state_file):
        harness = SetupHarness(state)
        async with harness.run_test(size=(80, 50)) as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._save_and_finish()
            await pilot.pause()

            await pilot.click("#done-btn")
            await pilot.pause()

            assert harness.dismissed is True


async def test_setup_cancel_dismisses_false(no_auto_setup):
    harness = SetupHarness(AppState())
    async with harness.run_test() as pilot:
        await pilot.pause(delay=0.3)
        await pilot.press("escape")
        await pilot.pause()
        assert harness.dismissed is False


async def test_setup_upload_options_with_gh(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = ssh_key
            screen._discovered_keys = [ssh_key]
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            assert "github-ssh" in screen._upload_actions
            assert screen.query_one("#upload-go", Button).disabled is False


async def test_setup_upload_options_gpg_shows_openpgp(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen.selected_key = gpg_key
            screen._discovered_keys = [gpg_key]
            screen.query_one(ContentSwitcher).current = "step-upload"
            await screen._populate_upload_options()
            await pilot.pause()

            assert "github-gpg" in screen._upload_actions
            assert "openpgp" in screen._upload_actions


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
        text = " ".join(str(lbl.content) for lbl in pilot.app.screen.query(Label))
        assert "500" in text
        assert "claude-haiku-4-5" in text


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
    def __init__(self, message: str) -> None:
        super().__init__()
        self.message = message
        self.dismissed: object = "not-yet"

    def on_mount(self) -> None:
        self.push_screen(PlatformErrorScreen(self.message), self._capture)

    def _capture(self, result: object) -> None:
        self.dismissed = result


async def test_platform_error_renders_message():
    harness = ErrorHarness("Can't run on this platform")
    async with harness.run_test() as pilot:
        await pilot.pause()
        text = " ".join(str(lbl.content) for lbl in pilot.app.screen.query(Label))
        assert "Can't run on this platform" in text


async def test_platform_error_quit_dismisses():
    harness = ErrorHarness("error")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#quit-btn")
        await pilot.pause()
        assert harness.dismissed is None


async def test_ccsentiment_app_engine_failure_shows_error_and_exits(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.resolve_engine", side_effect=RuntimeError("no engine")), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_pushes_setup_when_no_config(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.tui.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.detect_git_username", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, SetupScreen)


async def test_ccsentiment_app_claude_engine_shows_cost_review(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=50):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            assert pilot.app.screen.bucket_count == 50


async def test_ccsentiment_app_cost_cancel_exits(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_pipeline_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.resolve_engine", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=50), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_pipeline_run):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            await pilot.click("#cost-no")
            await pilot.pause(delay=0.3)
            mock_pipeline_run.assert_not_called()


async def test_ccsentiment_app_idle_when_no_work(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, (IdleEmpty, IdleCaughtUp))
            assert "all" in app.status_text.lower() or "set" in app.status_text.lower()


async def test_ccsentiment_app_rescan_clears_state(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(app.stage, IdleAfterUpload)

            await pilot.press("r")
            await pilot.pause()
            assert isinstance(app.stage, RescanConfirm)

            await pilot.press("r")
            await pilot.pause(delay=0.3)

    verify = Repository.open(db_path)
    try:
        assert verify.stats() == (0, 0, 0)
    finally:
        verify.close()


async def test_ccsentiment_app_runs_pipeline_and_uploads(tmp_path: Path, auth_ok):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        return records

    mock_upload = AsyncMock()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=2), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", mock_upload):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

            mock_upload.assert_awaited_once()


async def test_authenticate_returns_true_when_creds_valid(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is True


async def test_authenticate_returns_false_on_unreachable(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
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

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
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

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
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


async def test_run_flow_aborts_when_authenticate_returns_false(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
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
    assert format_duration(0) == "a few seconds"
    assert format_duration(29) == "a few seconds"


def test_format_duration_minutes():
    assert format_duration(60) == "~1 min"
    assert format_duration(900) == "~15 min"


def test_format_duration_hours():
    assert format_duration(3600) == "~1 hour"
    assert format_duration(7200) == "~2 hours"


def test_sample_payload_fields_match_real_record_schema():
    from cc_sentiment.models import SentimentRecord
    from cc_sentiment.tui import SAMPLE_PAYLOAD_LINES

    real_fields = set(SentimentRecord.model_fields)
    sample_fields = [k for k, _ in SAMPLE_PAYLOAD_LINES]

    for k in sample_fields:
        assert k in real_fields, f"{k!r} is not a real SentimentRecord field"
    for forbidden in ("message", "content", "transcript", "prompt_text", "prompt_body"):
        assert not any(forbidden in f for f in real_fields), (
            f"SentimentRecord has a field matching {forbidden!r} — sample payload may be misleading"
        )


async def test_set_total_renders_eta_when_hardware_estimates(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=10.0):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._set_total(1200, "omlx")
            assert "~2 min" in app.status_text
            assert "1200 buckets" in app.status_text


async def test_set_total_omits_eta_when_hardware_unknown(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._set_total(500, "omlx")
            assert "~" not in app.status_text
            assert "500 buckets" in app.status_text


async def test_add_buckets_updates_progress(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._set_total(100, "omlx")
            app._add_buckets(5)
            app._add_buckets(3)
            assert app.scored == 8


async def test_action_open_dashboard_opens_browser(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.tui.webbrowser.open") as mock_open:
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await pilot.press("o")
            await pilot.pause(delay=0.2)
            mock_open.assert_called_once_with(DASHBOARD_URL)
            assert DASHBOARD_URL in app.status_text


async def test_enter_idle_after_upload_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
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
            assert "O to open dashboard" in app.status_text


async def test_enter_idle_empty_state_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleEmpty)
            assert "no conversations yet" in app.status_text
            assert "O to browse" in app.status_text


async def test_successful_upload_lands_in_idle_after_upload(tmp_path: Path, auth_ok):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        return records

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=2), \
         patch("cc_sentiment.pipeline.Pipeline.run", side_effect=fake_run), \
         patch("cc_sentiment.upload.Uploader.upload", new_callable=AsyncMock):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)
            assert isinstance(app.stage, IdleAfterUpload)
            assert "sentiments.cc" in app.status_text


async def test_stage_transitions_across_successful_run(tmp_path: Path, auth_ok):
    records = [make_record(score=3)]
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        return records

    seen: list[type[Stage]] = []

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=1), \
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


async def test_rescan_confirm_restores_previous_stage_on_cancel(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    seed = Repository.open(db_path)
    seed.save_records("/fake.jsonl", 1.0, [make_record()])
    seed.close()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
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
