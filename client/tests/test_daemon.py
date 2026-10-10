from __future__ import annotations

import plistlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from cc_sentiment.cli import app
from cc_sentiment.daemon import Daemon, LaunchAgent
from cc_sentiment.headless import HeadlessNothingToDo, HeadlessOk, HeadlessUploadError

BINARY = Path("/Users/someone/.local/bin/cc-sentiment")


class TestRenderPlist:
    def test_runs_the_resident_daemon_under_keepalive(self) -> None:
        with patch("cc_sentiment.daemon.shutil.which", return_value="/opt/uv/bin/uv"):
            plist = plistlib.loads(LaunchAgent.render_plist(BINARY))
        assert plist["ProgramArguments"] == [str(BINARY), "daemon"]
        assert plist["RunAtLoad"] is True
        assert plist["KeepAlive"] is True
        assert plist["ThrottleInterval"] == Daemon.CYCLE_SECONDS
        assert "StartInterval" not in plist

    def test_path_leads_with_the_tool_and_uv_directories(self) -> None:
        with patch("cc_sentiment.daemon.shutil.which", return_value="/opt/uv/bin/uv"):
            plist = plistlib.loads(LaunchAgent.render_plist(BINARY))
        assert plist["EnvironmentVariables"]["PATH"].split(":") == [
            "/Users/someone/.local/bin",
            "/opt/uv/bin",
            "/opt/homebrew/bin",
            "/usr/local/bin",
            "/usr/bin",
            "/bin",
        ]

    def test_path_omits_uv_when_it_is_not_installed(self) -> None:
        with patch("cc_sentiment.daemon.shutil.which", return_value=None):
            plist = plistlib.loads(LaunchAgent.render_plist(BINARY))
        assert plist["EnvironmentVariables"]["PATH"].split(":")[:2] == [
            "/Users/someone/.local/bin",
            "/opt/homebrew/bin",
        ]


class TestServe:
    async def test_cycles_until_the_installed_version_changes(self) -> None:
        cycle = AsyncMock(return_value=HeadlessNothingToDo())
        sleep = AsyncMock()
        with patch.object(Daemon, "cycle", cycle), \
             patch("cc_sentiment.daemon.anyio.sleep", sleep), \
             patch("cc_sentiment.daemon.SelfUpdater.is_stale", side_effect=[False, False, True]):
            await Daemon.serve(False)
        assert cycle.await_count == 2
        assert [call.args for call in sleep.await_args_list] == [(Daemon.CYCLE_SECONDS,)] * 2

    async def test_logs_every_outcome_except_nothing_to_do(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        cycle = AsyncMock(side_effect=[
            HeadlessNothingToDo(),
            HeadlessOk(scored=3, uploaded=2),
            HeadlessUploadError(detail="couldn't reach server: boom"),
        ])
        with patch.object(Daemon, "cycle", cycle), \
             patch("cc_sentiment.daemon.anyio.sleep", AsyncMock()), \
             patch("cc_sentiment.daemon.SelfUpdater.is_stale", side_effect=[False, False, False, True]):
            await Daemon.serve(False)
        lines = capsys.readouterr().out.splitlines()
        assert [line.split(" ", 1)[1] for line in lines] == [
            "Scored 3, uploaded 2.",
            "couldn't reach server: boom",
        ]

    async def test_upgrades_in_place_of_sleeping_once_the_interval_elapses(self) -> None:
        upgrade = MagicMock()
        sleep = AsyncMock()
        with patch.object(Daemon, "cycle", AsyncMock(return_value=HeadlessNothingToDo())), \
             patch.object(Daemon, "UPGRADE_INTERVAL_SECONDS", -1), \
             patch("cc_sentiment.daemon.anyio.sleep", sleep), \
             patch("cc_sentiment.daemon.SelfUpdater.upgrade", upgrade), \
             patch("cc_sentiment.daemon.SelfUpdater.is_stale", side_effect=[False, True]):
            await Daemon.serve(False)
        upgrade.assert_called_once_with()
        sleep.assert_not_awaited()


class TestInstallCommand:
    def test_refuses_before_setup(self) -> None:
        with patch("cc_sentiment.daemon.LaunchAgent.install") as install:
            result = CliRunner().invoke(app, ["install"])
        assert result.exit_code == 2
        assert "cc-sentiment setup" in result.output
        install.assert_not_called()
