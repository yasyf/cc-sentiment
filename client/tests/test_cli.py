from __future__ import annotations

from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from cc_sentiment.cli import main
from cc_sentiment.models import AppState


class TestSingleCommand:
    def test_no_scan_subcommand(self) -> None:
        assert main.get_command(None, "scan") is None

    def test_no_upload_subcommand(self) -> None:
        assert main.get_command(None, "upload") is None

    def test_no_rescan_subcommand(self) -> None:
        assert main.get_command(None, "rescan") is None

    def test_setup_subcommand_public(self) -> None:
        cmd = main.get_command(None, "setup")
        assert cmd is not None
        assert cmd.hidden is False

    def test_run_subcommand_public(self) -> None:
        cmd = main.get_command(None, "run")
        assert cmd is not None
        assert cmd.hidden is False

    def test_benchmark_hidden(self) -> None:
        cmd = main.get_command(None, "benchmark")
        assert cmd is not None
        assert cmd.hidden is True

    def test_benchmark_not_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert "benchmark" not in result.output


class TestMainLaunchesApp:
    def test_default_invocation_launches_app(self) -> None:
        mock_app = MagicMock()
        with patch("cc_sentiment.tui.CCSentimentApp", return_value=mock_app) as app_cls, \
             patch.object(AppState, "load", return_value=AppState()):
            runner = CliRunner()
            result = runner.invoke(main, [])
            assert result.exit_code == 0, result.output
            app_cls.assert_called_once()
            mock_app.run.assert_called_once()

    def test_model_flag_forwarded(self) -> None:
        mock_app = MagicMock()
        with patch("cc_sentiment.tui.CCSentimentApp", return_value=mock_app) as app_cls, \
             patch.object(AppState, "load", return_value=AppState()):
            runner = CliRunner()
            runner.invoke(main, ["--model", "custom/model"])
            assert app_cls.call_args.kwargs["model_repo"] == "custom/model"
