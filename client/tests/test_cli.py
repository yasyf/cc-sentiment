from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from click.testing import CliRunner

from cc_sentiment.cli import main
from cc_sentiment.models import AppState, ProcessedFile, SSHConfig


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
