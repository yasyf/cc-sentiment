from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cc_sentiment.cli import main
from cc_sentiment.models import AppState, ClientConfig, ProcessedFile


class TestSetup:
    def test_saves_config_to_state(self, tmp_path: Path) -> None:
        state = AppState()
        state_file = tmp_path / "state.json"

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 1
        mock_subprocess_result.stdout = ""

        with patch("cc_sentiment.cli.subprocess.run", return_value=mock_subprocess_result), \
             patch(
                 "cc_sentiment.signing.KeyDiscovery.match_github_key",
                 return_value=Path("/home/.ssh/id_ed25519"),
             ), \
             patch.object(AppState, "load", return_value=state), \
             patch.object(AppState, "state_path", return_value=state_file), \
             patch.object(AppState, "save"):
            runner = CliRunner()
            result = runner.invoke(main, ["setup"], input="testuser\n")

        assert result.exit_code == 0
        assert "Configuration saved" in result.output
        assert state.config is not None
        assert state.config.github_username == "testuser"


class TestScan:
    def test_no_transcripts_prints_message(self) -> None:
        state = AppState(processed_files={"some_file": ProcessedFile(mtime=100.0)})

        with patch.object(AppState, "load", return_value=state), \
             patch(
                 "cc_sentiment.pipeline.Pipeline.discover_new_transcripts",
                 return_value=[],
             ):
            runner = CliRunner()
            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 0
        assert "up to date" in result.output

    def test_first_run_shows_welcome(self) -> None:
        state = AppState()

        with patch.object(AppState, "load", return_value=state), \
             patch(
                 "cc_sentiment.pipeline.Pipeline.discover_new_transcripts",
                 return_value=[],
             ):
            runner = CliRunner()
            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 0
        assert "Welcome to cc-sentiment" in result.output

    def test_first_run_no_transcripts_explains(self) -> None:
        state = AppState()

        with patch.object(AppState, "load", return_value=state), \
             patch(
                 "cc_sentiment.pipeline.Pipeline.discover_new_transcripts",
                 return_value=[],
             ):
            runner = CliRunner()
            result = runner.invoke(main, ["scan"])

        assert result.exit_code == 0
        assert "No Claude Code transcripts found" in result.output


class TestUploadCommand:
    def test_no_config_attempts_auto_setup(self) -> None:
        state = AppState(config=None)

        mock_subprocess_result = MagicMock()
        mock_subprocess_result.returncode = 1
        mock_subprocess_result.stdout = ""

        with patch.object(AppState, "load", return_value=state), \
             patch("cc_sentiment.cli.subprocess.run", return_value=mock_subprocess_result):
            runner = CliRunner()
            result = runner.invoke(main, ["upload"], input="\n")

        assert "auto-setup" in result.output.lower() or "Auto-setup" in result.output

    def test_no_pending_records(self) -> None:
        state = AppState(
            config=ClientConfig(
                github_username="testuser",
                key_path=Path("/home/.ssh/id_ed25519"),
            ),
        )

        with patch.object(AppState, "load", return_value=state):
            runner = CliRunner()
            result = runner.invoke(main, ["upload"])

        assert result.exit_code == 0
        assert "No pending" in result.output


class TestBenchmark:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "benchmark") is not None


class TestRescan:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "rescan") is not None
