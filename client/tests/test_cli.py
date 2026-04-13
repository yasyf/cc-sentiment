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
    def test_command_exists(self) -> None:
        assert main.get_command(None, "benchmark") is not None


class TestRescan:
    def test_command_exists(self) -> None:
        assert main.get_command(None, "rescan") is not None
