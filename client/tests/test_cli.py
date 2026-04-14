from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from cc_sentiment.cli import auto_setup, ensure_config, main
from cc_sentiment.models import AppState, ContributorId, GPGConfig, ProcessedFile, SSHConfig
from cc_sentiment.signing import GPGBackend, GPGKeyInfo, SSHBackend, SSHKeyInfo


class TestSetup:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "setup") is not None


class TestScan:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "scan") is not None


class TestUploadCommand:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "upload") is not None


class TestBenchmark:
    def test_command_hidden(self) -> None:
        cmd = main.get_command(None, "benchmark")
        assert cmd is not None
        assert cmd.hidden is True

    def test_benchmark_not_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "benchmark" not in result.output


class TestRescan:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "rescan") is not None


class TestDefaultCommand:
    def test_no_subcommand_triggers_scan(self) -> None:
        cmd = main.get_command(None, None)
        assert main.invoke_without_command is True


class TestAutoSetup:
    def test_ssh_key_already_on_github(self) -> None:
        state = AppState()
        with patch("cc_sentiment.cli.detect_git_username", return_value="testuser"), \
             patch("cc_sentiment.signing.KeyDiscovery.match_ssh_key", return_value=SSHBackend(private_key_path=Path("/home/.ssh/id_ed25519"))), \
             patch("cc_sentiment.upload.Uploader.verify_config", return_value=True), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is True
            assert isinstance(state.config, SSHConfig)
            assert state.config.contributor_id == ContributorId("testuser")

    def test_no_username_no_keys_returns_false(self) -> None:
        state = AppState()
        with patch("cc_sentiment.cli.detect_git_username", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.find_gpg_keys", return_value=()), \
             patch("cc_sentiment.cli.shutil.which", return_value=None), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is False

    def test_consent_gated_ssh_upload(self) -> None:
        state = AppState()
        ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.cli.detect_git_username", return_value="testuser"), \
             patch("cc_sentiment.signing.KeyDiscovery.match_ssh_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.match_gpg_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
             patch("cc_sentiment.signing.KeyDiscovery.find_gpg_keys", return_value=()), \
             patch("cc_sentiment.signing.KeyDiscovery.upload_github_ssh_key", return_value=True), \
             patch("cc_sentiment.cli.shutil.which", return_value="/usr/bin/gh"), \
             patch("cc_sentiment.cli.confirm_action", return_value=True), \
             patch("cc_sentiment.upload.Uploader.verify_config", return_value=True), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is True
            assert isinstance(state.config, SSHConfig)

    def test_consent_declined_falls_through(self) -> None:
        state = AppState()
        ssh_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="")
        with patch("cc_sentiment.cli.detect_git_username", return_value="testuser"), \
             patch("cc_sentiment.signing.KeyDiscovery.match_ssh_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.match_gpg_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.find_ssh_keys", return_value=(ssh_key,)), \
             patch("cc_sentiment.signing.KeyDiscovery.find_gpg_keys", return_value=()), \
             patch("cc_sentiment.cli.shutil.which", return_value="/usr/bin/gh"), \
             patch("cc_sentiment.cli.confirm_action", return_value=False), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is False

    def test_verify_failure_clears_config_and_continues(self) -> None:
        state = AppState()
        with patch("cc_sentiment.cli.detect_git_username", return_value="testuser"), \
             patch("cc_sentiment.signing.KeyDiscovery.match_ssh_key", return_value=SSHBackend(private_key_path=Path("/home/.ssh/id_ed25519"))), \
             patch("cc_sentiment.signing.KeyDiscovery.match_gpg_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.find_ssh_keys", return_value=()), \
             patch("cc_sentiment.signing.KeyDiscovery.find_gpg_keys", return_value=()), \
             patch("cc_sentiment.cli.shutil.which", return_value=None), \
             patch("cc_sentiment.upload.Uploader.verify_config", return_value=False), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is False
            assert state.config is None

    def test_generate_ssh_key_on_no_keys(self) -> None:
        state = AppState()
        new_key = SSHKeyInfo(path=Path("/home/.ssh/id_ed25519"), algorithm="ssh-ed25519", comment="cc-sentiment")
        with patch("cc_sentiment.cli.detect_git_username", return_value="testuser"), \
             patch("cc_sentiment.signing.KeyDiscovery.match_ssh_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.match_gpg_key", return_value=None), \
             patch("cc_sentiment.signing.KeyDiscovery.find_ssh_keys", return_value=()), \
             patch("cc_sentiment.signing.KeyDiscovery.find_gpg_keys", return_value=()), \
             patch("cc_sentiment.signing.KeyDiscovery.generate_ssh_key", return_value=new_key), \
             patch("cc_sentiment.signing.KeyDiscovery.upload_github_ssh_key", return_value=True), \
             patch("cc_sentiment.cli.shutil.which", return_value="/usr/bin/gh"), \
             patch("cc_sentiment.cli.confirm_action", return_value=True), \
             patch("cc_sentiment.upload.Uploader.verify_config", return_value=True), \
             patch.object(AppState, "save"):
            assert auto_setup(state) is True
            assert isinstance(state.config, SSHConfig)


class TestEnsureConfig:
    def test_existing_config_returns_immediately(self) -> None:
        state = AppState(
            config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=Path("/home/.ssh/id_ed25519")),
        )
        with patch("pathlib.Path.exists", return_value=True):
            ensure_config(state)
        assert state.config is not None

    def test_missing_key_clears_config(self, tmp_path: Path) -> None:
        state_file = tmp_path / "state.json"
        missing_key = tmp_path / "gone_key"
        state = AppState(
            config=SSHConfig(contributor_id=ContributorId("testuser"), key_path=missing_key),
        )

        with patch.object(AppState, "state_path", return_value=state_file), \
             patch("cc_sentiment.cli.auto_setup", return_value=True):
            ensure_config(state)

    def test_gpg_config_not_affected_by_key_check(self) -> None:
        state = AppState(
            config=GPGConfig(contributor_type="github", contributor_id=ContributorId("testuser"), fpr="ABCD1234"),
        )
        ensure_config(state)
        assert state.config is not None
