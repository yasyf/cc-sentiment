from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from textual.widgets import Button, ContentSwitcher, DataTable, Input, Label, RadioSet

from cc_sentiment.models import AppState, GPGConfig, SSHConfig
from cc_sentiment.signing import GPGKeyInfo, SSHKeyInfo
from cc_sentiment.tui import SetupApp


async def test_mounts_all_steps():
    async with SetupApp().run_test() as pilot:
        for step in ("step-username", "step-discovery", "step-remote", "step-upload", "step-done"):
            assert pilot.app.query_one(f"#{step}") is not None


async def test_starts_on_username_step():
    async with SetupApp().run_test() as pilot:
        assert pilot.app.query_one(ContentSwitcher).current == "step-username"


async def test_empty_username_blocked():
    async with SetupApp().run_test() as pilot:
        pilot.app.query_one("#username-input", Input).value = ""
        await pilot.click("#username-next")
        await pilot.pause()
        assert pilot.app.query_one(ContentSwitcher).current == "step-username"


async def test_auto_detect_username():
    with patch("cc_sentiment.tui.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0, stdout="testuser\n")
        async with SetupApp().run_test() as pilot:
            await pilot.pause(delay=0.5)
            assert pilot.app.query_one("#username-input", Input).value == "testuser"


async def test_key_discovery_populates_table():
    ssh_keys = (SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host"),)

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.httpx.get", return_value=MagicMock(status_code=200)), \
         patch("cc_sentiment.tui.KeyDiscovery.find_ssh_keys", return_value=ssh_keys), \
         patch("cc_sentiment.tui.KeyDiscovery.find_gpg_keys", return_value=()):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert pilot.app.query_one("#key-table", DataTable).row_count == 1
            assert pilot.app.query_one("#discovery-next", Button).disabled is False


async def test_no_keys_disables_next_without_gpg():
    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.KeyDiscovery.find_ssh_keys", return_value=()), \
         patch("cc_sentiment.tui.KeyDiscovery.find_gpg_keys", return_value=()), \
         patch("cc_sentiment.tui.KeyDiscovery.has_tool", return_value=False):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app._switch_to_discovery()
            await pilot.pause(delay=0.5)

            assert pilot.app.query_one("#key-table", DataTable).row_count == 0
            assert getattr(pilot.app, "_generate_gpg", None) is False


async def test_remote_check_ssh_found():
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 AAAA key1",)), \
         patch("cc_sentiment.tui.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app.query_one(ContentSwitcher).current = "step-remote"
            pilot.app.check_remotes()
            await pilot.pause(delay=0.5)

            assert pilot.app._key_on_remote is True
            assert pilot.app.query_one("#remote-next", Button).disabled is False


async def test_remote_check_ssh_not_found():
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="user@host")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.KeyDiscovery.fetch_github_ssh_keys", return_value=("ssh-ed25519 BBBB other",)), \
         patch("cc_sentiment.tui.SSHBackend.fingerprint", return_value="ssh-ed25519 AAAA"):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app.query_one(ContentSwitcher).current = "step-remote"
            pilot.app.check_remotes()
            await pilot.pause(delay=0.5)

            assert pilot.app._key_on_remote is False


async def test_save_ssh_config(tmp_path: Path):
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch.object(AppState, "load", return_value=AppState()):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app._save_and_finish()
            await pilot.pause()

            loaded = AppState.model_validate_json(state_file.read_text())
            assert isinstance(loaded.config, SSHConfig)
            assert loaded.config.github_username == "testuser"
            assert loaded.config.key_path == Path("/home/.ssh/id_ed25519")


async def test_save_gpg_config(tmp_path: Path):
    state_file = tmp_path / "state.json"
    gpg_key = GPGKeyInfo(fpr="F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch.object(AppState, "load", return_value=AppState()):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = gpg_key
            pilot.app._save_and_finish()
            await pilot.pause()

            loaded = AppState.model_validate_json(state_file.read_text())
            assert isinstance(loaded.config, GPGConfig)
            assert loaded.config.fpr == "F3299DE3FE0F6C3CF2B66BFBF7ECDD88A700D73A"


async def test_done_navigates_to_done_step(tmp_path: Path):
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch.object(AppState, "load", return_value=AppState()):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app._save_and_finish()
            await pilot.pause()

            assert pilot.app.query_one(ContentSwitcher).current == "step-done"


async def test_upload_options_with_gh():
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app._discovered_keys = [ssh_key]
            pilot.app.query_one(ContentSwitcher).current = "step-upload"
            pilot.app._populate_upload_options()
            await pilot.pause()

            assert "github-ssh" in pilot.app._upload_actions
            assert pilot.app.query_one("#upload-go", Button).disabled is False


async def test_upload_options_without_gh():
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app._discovered_keys = [ssh_key]
            pilot.app.query_one(ContentSwitcher).current = "step-upload"
            pilot.app._populate_upload_options()
            await pilot.pause()

            buttons = list(pilot.app.query_one("#upload-options", RadioSet).query("RadioButton"))
            assert buttons[0].disabled is True


async def test_gpg_key_upload_shows_openpgp_option():
    gpg_key = GPGKeyInfo(fpr="ABCDEF1234567890", email="test@example.com", algo="rsa4096")

    with patch("cc_sentiment.tui.shutil.which", return_value="/usr/bin/gh"), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.GPGBackend.public_key_text", return_value="-----BEGIN PGP PUBLIC KEY BLOCK-----"):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = gpg_key
            pilot.app._discovered_keys = [gpg_key]
            pilot.app.query_one(ContentSwitcher).current = "step-upload"
            pilot.app._populate_upload_options()
            await pilot.pause()

            assert "github-gpg" in pilot.app._upload_actions
            assert "openpgp" in pilot.app._upload_actions


async def test_skip_upload_saves_config(tmp_path: Path):
    state_file = tmp_path / "state.json"
    ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")

    with patch("cc_sentiment.tui.shutil.which", return_value=None), \
         patch("cc_sentiment.tui.subprocess.run", return_value=MagicMock(returncode=1, stdout="")), \
         patch("cc_sentiment.tui.SSHBackend.public_key_text", return_value="ssh-ed25519 AAAA key"), \
         patch.object(AppState, "state_path", return_value=state_file), \
         patch.object(AppState, "load", return_value=AppState()):
        async with SetupApp().run_test() as pilot:
            pilot.app.username = "testuser"
            pilot.app.selected_key = ssh_key
            pilot.app._discovered_keys = [ssh_key]
            pilot.app.query_one(ContentSwitcher).current = "step-upload"
            pilot.app._populate_upload_options()
            await pilot.pause()

            await pilot.click("#upload-skip")
            await pilot.pause()

            assert pilot.app.query_one(ContentSwitcher).current == "step-done"
            loaded = AppState.model_validate_json(state_file.read_text())
            assert isinstance(loaded.config, SSHConfig)
