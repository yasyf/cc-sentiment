from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from textual.app import App
from textual.containers import Vertical
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label, Static

from cc_sentiment.models import AppState, ContributorId, GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui import CCSentimentApp
from cc_sentiment.tui.boot_view import EngineBootView, HighlightSpan, WindowedSlice
from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.screens import (
    CostReviewScreen,
    PlatformErrorScreen,
    SetupScreen,
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
from cc_sentiment.tui.widgets import HourlyChart
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
    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value=None), \
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


@pytest.fixture
def no_stat_share():
    with patch.object(CCSentimentApp, "_poll_card", new_callable=AsyncMock):
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
    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, "testuser")), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value="testuser"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one("#username-input", Input).value == "testuser"


async def test_setup_auto_success_jumps_to_done():
    state = AppState()

    async def fake_run(self) -> tuple[bool, str | None]:
        self.state.config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        return True, "testuser"

    with patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new=fake_run), \
         patch.object(AppState, "save"):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one(ContentSwitcher).current == "step-done"


async def test_setup_key_discovery_populates_table(no_auto_setup):
    ssh_keys = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host"),)

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=ssh_keys), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.query_one("#key-table", DataTable).row_count == 1
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_setup_no_keys_without_gpg_disables_next(no_auto_setup):
    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.has_tool", return_value=False), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=False):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen.query_one("#key-table", DataTable).row_count == 0
            assert getattr(screen, "_generation_mode", "unset") is None


async def test_setup_remote_check_ssh_found(no_auto_setup):
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",)), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
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

    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 BBBB other",)), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
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

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
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


async def test_try_existing_gist_returns_config_when_found():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.status.KeyDiscovery.find_cc_sentiment_gist_id", return_value="abcdef1234567890abcd"):
        result = await setup.try_existing_gist("octocat")

    assert isinstance(result, GistConfig)
    assert result.contributor_id == ContributorId("octocat")
    assert result.gist_id == "abcdef1234567890abcd"


async def test_try_existing_gist_returns_none_when_no_local_keypair():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=None):
        assert await setup.try_existing_gist("octocat") is None


async def test_try_existing_gist_returns_none_when_gh_not_authed():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=False):
        assert await setup.try_existing_gist("octocat") is None


async def test_try_existing_gist_returns_none_when_no_gist_found():
    from cc_sentiment.tui.status import AutoSetup, StatusEmitter
    from textual.widgets import Static

    state = AppState()
    widget = Static()
    emit = StatusEmitter(widget=widget)
    setup = AutoSetup(state, emit)

    with patch("cc_sentiment.tui.status.KeyDiscovery.find_gist_keypair", return_value=Path("/home/.cc-sentiment/keys/id_ed25519")), \
         patch("cc_sentiment.tui.status.KeyDiscovery.gh_authenticated", return_value=True), \
         patch("cc_sentiment.tui.status.KeyDiscovery.find_cc_sentiment_gist_id", return_value=None):
        assert await setup.try_existing_gist("octocat") is None


async def test_setup_no_keys_with_gh_auth_uses_gist_mode(no_auto_setup):
    with patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert screen._generation_mode == "gist"
            assert screen.query_one("#discovery-next", Button).disabled is False


async def test_generate_gist_key_saves_gist_config(tmp_path: Path, no_auto_setup, auth_ok):
    state = AppState()
    state_file = tmp_path / "state.json"
    key_path = tmp_path / "id_ed25519"

    with patch.object(AppState, "state_path", return_value=state_file), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.generate_gist_keypair", return_value=key_path), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.create_gist", return_value="abcdef1234567890abcd"), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.screens.setup.KeyDiscovery.gh_authenticated", return_value=True):
        async with SetupHarness(state).run_test() as pilot:
            await pilot.pause(delay=0.3)
            screen = pilot.app.screen
            screen.username = "testuser"
            screen._switch_to_discovery()
            await pilot.pause(delay=0.5)

            await pilot.click("#discovery-next")
            await pilot.pause(delay=0.5)

            assert isinstance(state.config, GistConfig)
            assert state.config.contributor_id == ContributorId("testuser")
            assert state.config.gist_id == "abcdef1234567890abcd"
            assert state.config.key_path == key_path


async def test_setup_upload_options_gpg_shows_openpgp(no_auto_setup):
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.screens.setup.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.screens.setup.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
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
        text = " ".join(
            str(w.render()) for w in pilot.app.screen.query("Label, Static")
        )
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
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", side_effect=RuntimeError("no engine")), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_debug_mode_composes(tmp_path: Path):
    from cc_sentiment.tui.widgets.debug_section import DebugSection

    state = AppState()
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", side_effect=RuntimeError("no engine")), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path, debug=True)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.1)
            assert pilot.app.query_one(DebugSection) is not None


async def test_ccsentiment_app_pushes_setup_when_no_config(tmp_path: Path):
    state = AppState()
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan(Path("/fake.jsonl"), 1))), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.run", new_callable=AsyncMock, return_value=(False, None)), \
         patch("cc_sentiment.tui.screens.setup.AutoSetup.find_git_username", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, SetupScreen)


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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert await app._authenticate() is True


async def test_authenticate_returns_false_on_unreachable(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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


async def test_run_flow_aborts_when_authenticate_returns_false(tmp_path: Path):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    mock_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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


def test_sample_payload_fields_match_real_record_schema():
    from cc_sentiment.models import SentimentRecord

    payload = SetupScreen.render_sample_payload()
    real_fields = set(SentimentRecord.model_fields)
    sample_fields = [
        line.split('"')[1]
        for line in payload.split("\n")
        if line.strip().startswith("[cyan]")
    ]

    for k in sample_fields:
        assert k in real_fields, f"{k!r} is not a real SentimentRecord field"
    for forbidden in ("message", "content", "transcript", "prompt_text", "prompt_body"):
        assert not any(forbidden in f for f in real_fields), (
            f"SentimentRecord has a field matching {forbidden!r} — sample payload may be misleading"
        )


async def test_set_total_renders_eta_when_hardware_estimates(tmp_path: Path, auth_ok):
    from textual.widgets import Label
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=10.0):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(1200, "omlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:02:00" in label_text
            assert "Scoring locally on your Mac" in app.status_text


async def test_set_total_omits_eta_when_hardware_unknown(tmp_path: Path, auth_ok):
    from textual.widgets import Label
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())), \
         patch("cc_sentiment.hardware.Hardware.estimate_buckets_per_sec", return_value=None):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(500, "omlx", 0)
            label_text = str(app.query_one("#progress-label", Label).render())
            assert "00:00:00" in label_text
            assert "Scoring locally on your Mac" in app.status_text


async def test_add_buckets_updates_progress(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            app._begin_scoring(100, "omlx", 0)
            app._add_buckets(5)
            app._add_buckets(3)
            assert app.scored == 8


async def test_action_open_dashboard_opens_browser(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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
            assert "O to open dashboard" in app.status_text


async def test_enter_idle_empty_state_mentions_dashboard(tmp_path: Path, auth_ok):
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    db_path = tmp_path / "records.db"

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            await app._enter_idle(uploaded=False)
            assert isinstance(app.stage, IdleEmpty)
            assert "No conversations yet" in app.status_text
            assert "O to browse" in app.status_text


async def test_successful_upload_lands_in_idle_after_upload(tmp_path: Path, auth_ok, no_stat_share):
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))
    db_path = tmp_path / "records.db"

    async def fake_run(repo, *args, on_transcript_complete=lambda _: None, **kwargs):
        repo.save_records("/fake.jsonl", 0.0, records)
        on_transcript_complete(records)
        return records

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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

    with patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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


async def test_hourly_chart_renders_dot_and_line_grid():
    from datetime import datetime, timezone

    records = [
        make_record(score=5, time=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)),
        make_record(score=1, time=datetime(2026, 4, 10, 14, 0, tzinfo=timezone.utc)),
        make_record(score=3, time=datetime(2026, 4, 10, 20, 0, tzinfo=timezone.utc)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        text = str(chart.content)
        lines = text.split("\n")
        assert len(lines) == 7
        for tick in HourlyChart.Y_TICKS.values():
            assert any(line.startswith(tick) for line in lines[:5])
        assert "─" * 24 in lines[5]
        assert "12a" in lines[6]
        assert "6a" in lines[6]
        assert "12p" in lines[6]
        assert "[red]●[/]" in lines[4]
        assert "[cyan]●[/]" in lines[0]


async def test_hourly_chart_scales_frustration_relative_to_max():
    from datetime import datetime, timezone

    records = [
        make_record(score=1, time=datetime(2026, 4, 10, 8, 0, tzinfo=timezone.utc)),
        make_record(score=2, time=datetime(2026, 4, 10, 8, 1, tzinfo=timezone.utc)),
        make_record(score=1, time=datetime(2026, 4, 10, 9, 0, tzinfo=timezone.utc)),
        make_record(score=4, time=datetime(2026, 4, 10, 10, 0, tzinfo=timezone.utc)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        lines = str(chart.content).split("\n")
        assert "[red]●[/]" in lines[4]
        assert "[yellow]●[/]" in lines[2]
        assert "[cyan]●[/]" in lines[0]


async def test_hourly_chart_empty_records():
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart([])
        await pilot.pause()
        assert "no data yet" in str(chart.content)


class EngineBootHarness(App[None]):
    def compose(self):
        with Vertical(id="section"):
            yield Static("", id="log")


async def test_engine_boot_snippet_survives_bracket_heavy_content():
    async with EngineBootHarness().run_test() as pilot:
        boot = EngineBootView(
            app=pilot.app,
            section=pilot.app.query_one("#section"),
            log=pilot.app.query_one("#log", Static),
        )
        boot.show("test-engine")
        await boot.add_snippet(
            "2026-04-03T11:14:13.287367+0000 +13m26s [🐞][DSPyCompilationServer.compile] 'ignore'",
            1,
        )
        boot.last_snippet_at = 0.0
        await boot.add_snippet("prefix text [dim", 1)
        boot.last_snippet_at = 0.0
        await boot.add_snippet("<task-notification> <task-id>abc</task-id> body", 5)
        await pilot.pause()
        assert len(boot.lines) >= 1


@dataclass
class FakeToken:
    idx: int
    text: str
    pos_: str
    lemma_: str


def test_slice_window_both_ellipses_center():
    full = "a" * 30 + "BUG" + "b" * 30
    anchor = HighlightSpan(start=30, end=33, color="red", priority=2)
    slice_ = EngineBootView.slice_window(full, anchor, width=20)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.body.endswith("…")
    assert "BUG" in slice_.body
    assert len(slice_.body) == 20


def test_slice_window_drops_leading_near_start():
    full = "bug " + "a" * 100
    anchor = HighlightSpan(start=0, end=3, color="red", priority=2)
    slice_ = EngineBootView.slice_window(full, anchor, width=20)
    assert not slice_.leading
    assert not slice_.body.startswith("…")
    assert slice_.body.endswith("…")
    assert slice_.body.startswith("bug")
    assert len(slice_.body) == 20


def test_slice_window_drops_trailing_near_end():
    full = "a" * 100 + " bug"
    anchor = HighlightSpan(start=101, end=104, color="red", priority=2)
    slice_ = EngineBootView.slice_window(full, anchor, width=20)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.body.endswith("bug")
    assert len(slice_.body) == 20


def test_slice_window_returns_full_when_short():
    full = "short text with bug"
    anchor = HighlightSpan(start=16, end=19, color="red", priority=2)
    slice_ = EngineBootView.slice_window(full, anchor, width=60)
    assert slice_.body == full
    assert slice_.full_offset == 0
    assert not slice_.leading


def test_apply_styles_translates_indices_into_body():
    slice_ = WindowedSlice(body="…abc bug def…", full_offset=30, kept_len=11, leading=True)
    candidates = [HighlightSpan(start=34, end=37, color="red", priority=2)]
    text = EngineBootView.apply_styles(slice_, candidates)
    assert any(
        str(s.style) == "red" and (s.start, s.end) == (5, 8)
        for s in text.spans
    )


def test_apply_styles_drops_out_of_window_candidates():
    slice_ = WindowedSlice(body="…abc bug def…", full_offset=30, kept_len=11, leading=True)
    candidates = [HighlightSpan(start=100, end=103, color="green", priority=2)]
    text = EngineBootView.apply_styles(slice_, candidates)
    assert not list(text.spans)


def test_apply_styles_skips_empty_color():
    slice_ = WindowedSlice(body="hello", full_offset=0, kept_len=5, leading=False)
    candidates = [HighlightSpan(start=0, end=5, color="", priority=1)]
    text = EngineBootView.apply_styles(slice_, candidates)
    assert not list(text.spans)


def test_collect_candidates_tags_profanity_and_lemmas():
    full = "this is perfect but the bug is broken"
    tokens = [
        FakeToken(idx=0, text="this", pos_="PRON", lemma_="this"),
        FakeToken(idx=5, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=8, text="perfect", pos_="ADJ", lemma_="perfect"),
        FakeToken(idx=16, text="but", pos_="CCONJ", lemma_="but"),
        FakeToken(idx=20, text="the", pos_="DET", lemma_="the"),
        FakeToken(idx=24, text="bug", pos_="NOUN", lemma_="bug"),
        FakeToken(idx=28, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=31, text="broken", pos_="ADJ", lemma_="broken"),
    ]
    candidates = EngineBootView.collect_candidates(full, tokens, score=2)
    colors = {(c.start, c.color) for c in candidates}
    assert (8, "green") in colors
    assert (24, "red") in colors
    assert (31, "red") in colors


def test_collect_candidates_catches_frustration_pattern_for_low_scores():
    full = "wtf is happening here, completely useless"
    candidates = EngineBootView.collect_candidates(full, [], score=1)
    assert any(c.priority == 3 and c.color == "red" for c in candidates)


def test_fallback_anchor_picks_longest_content_word():
    tokens = [
        FakeToken(idx=0, text="keep", pos_="VERB", lemma_="keep"),
        FakeToken(idx=5, text="monitoring", pos_="VERB", lemma_="monitor"),
        FakeToken(idx=16, text="it", pos_="PRON", lemma_="it"),
        FakeToken(idx=19, text="goes", pos_="VERB", lemma_="go"),
    ]
    anchor = EngineBootView.fallback_anchor(tokens)
    assert anchor is not None
    assert (anchor.start, anchor.end) == (5, 15)
    assert anchor.color == ""
    assert anchor.priority == 1


def test_fallback_anchor_never_colors_by_score():
    tokens = [FakeToken(idx=0, text="thing", pos_="NOUN", lemma_="thing")]
    anchor = EngineBootView.fallback_anchor(tokens)
    assert anchor is not None
    assert anchor.color == ""


def test_fallback_anchor_returns_none_when_no_eligible_token():
    tokens = [
        FakeToken(idx=0, text="is", pos_="VERB", lemma_="be"),
        FakeToken(idx=3, text="a", pos_="DET", lemma_="a"),
        FakeToken(idx=5, text="42", pos_="NUM", lemma_="42"),
    ]
    assert EngineBootView.fallback_anchor(tokens) is None


def test_windowed_highlight_prefix_fallback_applies_frustration_regex():
    text = EngineBootView.windowed_highlight("wtf this is broken", score=2)
    assert any(
        str(s.style) == "red" and s.start == 0 and s.end == 3
        for s in text.spans
    )


def test_windowed_highlight_prefix_fallback_truncates_when_no_nlp():
    long = "x" * 200
    text = EngineBootView.windowed_highlight(long, score=4)
    assert len(text.plain) == EngineBootView.MAX_SNIPPET_CHARS
    assert text.plain.endswith("…")


@pytest.fixture
def real_nlp(monkeypatch):
    spacy = pytest.importorskip("spacy")
    try:
        model = spacy.load("en_core_web_sm", disable=["parser"])
    except OSError:
        pytest.skip("spaCy en_core_web_sm not available")
    monkeypatch.setattr("cc_sentiment.nlp.NLP.model", model)
    return model


def test_windowed_highlight_anchors_on_profanity_past_prefix(real_nlp):
    prefix = (
        "neutral filler text that says nothing special just padding "
        "here too here still more filler going on and on and on "
    )
    assert len(prefix) > EngineBootView.MAX_SNIPPET_CHARS
    full = prefix + "fuck this"
    text = EngineBootView.windowed_highlight(full, score=1)
    assert "fuck" in text.plain
    assert any(str(s.style) == "red" for s in text.spans)


def test_windowed_highlight_leaves_neutral_message_uncolored(real_nlp):
    full = (
        "keep monitoring it as it goes and give me an updated ETA for "
        "the server deployment so we can plan the rest of the launch"
    )
    for score in (1, 2, 3, 4, 5):
        text = EngineBootView.windowed_highlight(full, score=score)
        assert not list(text.spans), f"score={score} produced spans {list(text.spans)}"


def test_windowed_highlight_colors_stop_red_even_in_positive_bucket(real_nlp):
    text = EngineBootView.windowed_highlight("STOP GUESSING", score=4)
    assert any(str(s.style) == "red" for s in text.spans)
    assert not any(str(s.style) == "green" for s in text.spans)


def test_windowed_highlight_colors_continue_green_even_in_negative_bucket(real_nlp):
    text = EngineBootView.windowed_highlight("Continue from where you left off.", score=2)
    assert any(str(s.style) == "green" for s in text.spans)
    assert not any(str(s.style) == "red" for s in text.spans)


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
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app.view.cta.schedule_available is True
            assert app.view.cta.showing == "schedule"
            section = pilot.app.query_one("#cta-section")
            assert "inactive" not in section.classes
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Schedule it"


async def test_cta_hides_when_daemon_installed_and_no_tweet(tmp_path: Path, auth_ok, no_stat_share):
    state = AppState(config=GITHUB_CONFIG)
    db_path = tmp_path / "records.db"
    with patch("cc_sentiment.tui.app.LaunchAgent.is_installed", return_value=True), \
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.scan", AsyncMock(return_value=make_scan())):
        app = CCSentimentApp(state=state, db_path=db_path)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            app.view.set_tweet(GITHUB_CONFIG, STAT)
            await pilot.pause()
            assert app.view.cta.showing == "schedule"
            button = pilot.app.query_one("#cta-action", Button)
            assert str(button.label) == "Schedule it"

            app.view.rotate_cta()
            await pilot.pause()
            assert app.view.cta.showing == "tweet"
            assert str(button.label) == "Tweet it"
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
         patch("cc_sentiment.tui.app.EngineFactory.resolve", return_value="omlx"), \
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


async def test_card_poller_invokes_on_ready_when_stat_arrives():
    from cc_sentiment.tui.screens.stat_share import CardPoller

    calls: list[MyStat] = []
    states: list[tuple[int, str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        return_value=STAT,
    ):
        poller = CardPoller(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda a, s, e, stop: states.append((a, s, e, stop)),
        )
        await poller.run()

    assert calls == [STAT]
    assert any(state[3] == "ready" for state in states)


async def test_card_poller_gives_up_when_max_duration_exceeded():
    from cc_sentiment.tui.screens.stat_share import CardPoller

    calls: list[MyStat] = []
    states: list[tuple[int, str, float, str | None]] = []

    with patch(
        "cc_sentiment.upload.Uploader.fetch_my_stat",
        new_callable=AsyncMock,
        side_effect=httpx.ConnectError("no net"),
    ), patch.object(CardPoller, "MAX_POLL_SECONDS", 0.0):
        poller = CardPoller(
            config=GITHUB_CONFIG,
            on_ready=calls.append,
            on_state=lambda a, s, e, stop: states.append((a, s, e, stop)),
        )
        await poller.run()

    assert calls == []
    assert any(state[3] == "timeout" for state in states)
