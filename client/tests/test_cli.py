from __future__ import annotations

from unittest.mock import MagicMock, patch

from typer.models import CommandInfo
from typer.testing import CliRunner

from cc_sentiment.cli import app
from cc_sentiment.models import AppState


def find_command(name: str) -> CommandInfo | None:
    return next(
        (
            ci
            for ci in app.registered_commands
            if (ci.name or (ci.callback.__name__ if ci.callback else "")) == name
        ),
        None,
    )


class TestSingleCommand:
    def test_no_scan_subcommand(self) -> None:
        assert find_command("scan") is None

    def test_no_upload_subcommand(self) -> None:
        assert find_command("upload") is None

    def test_no_rescan_subcommand(self) -> None:
        assert find_command("rescan") is None

    def test_setup_subcommand_public(self) -> None:
        cmd = find_command("setup")
        assert cmd is not None
        assert cmd.hidden is False

    def test_run_subcommand_public(self) -> None:
        cmd = find_command("run")
        assert cmd is not None
        assert cmd.hidden is False

    def test_benchmark_hidden(self) -> None:
        cmd = find_command("benchmark")
        assert cmd is not None
        assert cmd.hidden is True

    def test_benchmark_not_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert "benchmark" not in result.output

    def test_lookup_hidden(self) -> None:
        cmd = find_command("lookup")
        assert cmd is not None
        assert cmd.hidden is True

    def test_lookup_not_in_help(self) -> None:
        runner = CliRunner()
        result = runner.invoke(app, ["--help"])
        assert "lookup" not in result.output

    def test_lookup_unknown_hash_exits_nonzero(self) -> None:
        runner = CliRunner()
        with patch("cc_sentiment.repo.Repository.open") as repo_open:
            mock_repo = MagicMock()
            mock_repo.all_records.return_value = []
            mock_repo.close = MagicMock()
            repo_open.return_value = mock_repo
            result = runner.invoke(app, ["lookup", "deadbeef"])
        assert result.exit_code == 1
        assert "No bucket found" in result.output


class TestMainLaunchesApp:
    def test_default_invocation_launches_app(self) -> None:
        mock_app = MagicMock()
        with patch("cc_sentiment.tui.CCSentimentApp", return_value=mock_app) as app_cls, \
             patch.object(AppState, "load", return_value=AppState()):
            runner = CliRunner()
            result = runner.invoke(app, [])
            assert result.exit_code == 0, result.output
            app_cls.assert_called_once()
            mock_app.run.assert_called_once()

    def test_model_flag_forwarded(self) -> None:
        mock_app = MagicMock()
        with patch("cc_sentiment.tui.CCSentimentApp", return_value=mock_app) as app_cls, \
             patch.object(AppState, "load", return_value=AppState()):
            runner = CliRunner()
            runner.invoke(app, ["--model", "custom/model"])
            assert app_cls.call_args.kwargs["model_repo"] == "custom/model"
