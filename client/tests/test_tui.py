from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import App
from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label

from cc_sentiment.models import AppState, ContributorId, GPGConfig, SSHConfig
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui import (
    CCSentimentApp,
    CostReviewScreen,
    PlatformErrorScreen,
    SetupScreen,
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
    with patch("cc_sentiment.tui.auto_setup_silent", return_value=False), \
         patch("cc_sentiment.tui.detect_git_username", return_value=None):
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
    with patch("cc_sentiment.tui.auto_setup_silent", return_value=False), \
         patch("cc_sentiment.tui.detect_git_username", return_value="testuser"):
        async with SetupHarness(AppState()).run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert pilot.app.screen.query_one("#username-input", Input).value == "testuser"


async def test_setup_auto_success_jumps_to_done():
    state = AppState()

    def fake_auto_setup(s: AppState) -> bool:
        s.config = SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519"))
        return True

    with patch("cc_sentiment.tui.auto_setup_silent", side_effect=fake_auto_setup), \
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
        async with harness.run_test() as pilot:
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
            screen._populate_upload_options()
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
            screen._populate_upload_options()
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


async def test_ccsentiment_app_engine_failure_shows_error_and_exits():
    state = AppState()
    with patch("cc_sentiment.tui.resolve_engine", side_effect=RuntimeError("no engine")), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.3)
            assert isinstance(pilot.app.screen, PlatformErrorScreen)


async def test_ccsentiment_app_pushes_setup_when_no_config():
    state = AppState()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.tui.auto_setup_silent", return_value=False), \
         patch("cc_sentiment.tui.detect_git_username", return_value=None), \
         patch("cc_sentiment.upload.Uploader.records_from_state", return_value=[]):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, SetupScreen)


async def test_ccsentiment_app_claude_engine_shows_cost_review():
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))

    with patch("cc_sentiment.tui.resolve_engine", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=50), \
         patch("cc_sentiment.upload.Uploader.records_from_state", return_value=[]):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            assert pilot.app.screen.bucket_count == 50


async def test_ccsentiment_app_cost_cancel_exits():
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))

    mock_pipeline_run = AsyncMock(return_value=[])
    with patch("cc_sentiment.tui.resolve_engine", return_value="claude"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.count_new_buckets", return_value=50), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_pipeline_run), \
         patch("cc_sentiment.upload.Uploader.records_from_state", return_value=[]):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert isinstance(pilot.app.screen, CostReviewScreen)
            await pilot.click("#cost-no")
            await pilot.pause(delay=0.3)
            mock_pipeline_run.assert_not_called()


async def test_ccsentiment_app_idle_when_no_work():
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.upload.Uploader.records_from_state", return_value=[]):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app._rescan_armed is True
            assert "all" in app.status_text.lower() or "set" in app.status_text.lower()


async def test_ccsentiment_app_rescan_clears_state():
    state = AppState(config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")))
    state.processed_files = {"/fake.jsonl": MagicMock()}

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[]), \
         patch("cc_sentiment.upload.Uploader.records_from_state", return_value=[]), \
         patch.object(AppState, "save"):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert app._rescan_armed is True

            await pilot.press("r")
            await pilot.pause()
            assert app._rescan_pending is True

            await pilot.press("r")
            await pilot.pause(delay=0.3)
            assert state.processed_files == {}


async def test_ccsentiment_app_runs_pipeline_and_uploads():
    records = [make_record(score=3), make_record(score=4)]
    state = AppState(config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"))

    mock_pipeline_run = AsyncMock(return_value=records)
    mock_upload = AsyncMock()

    with patch("cc_sentiment.tui.resolve_engine", return_value="omlx"), \
         patch("cc_sentiment.pipeline.Pipeline.discover_new_transcripts", return_value=[(Path("/fake.jsonl"), 0.0)]), \
         patch("cc_sentiment.pipeline.Pipeline.run", mock_pipeline_run), \
         patch("cc_sentiment.upload.Uploader.records_from_state", side_effect=[[], records]), \
         patch("cc_sentiment.upload.Uploader.upload", mock_upload):
        app = CCSentimentApp(state=state)
        async with app.run_test() as pilot:
            await pilot.pause(delay=1.0)

            mock_pipeline_run.assert_awaited_once()
            mock_upload.assert_awaited_once()
