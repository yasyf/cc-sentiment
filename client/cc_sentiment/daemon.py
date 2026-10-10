from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread

from cc_sentiment.headless import HeadlessNothingToDo, HeadlessOutcome, HeadlessRunner
from cc_sentiment.models import AppState
from cc_sentiment.repo import Repository
from cc_sentiment.updater import SelfUpdater

LABEL = "cc.sentiments.agent"
SYSTEM_PATH = ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin")


class Daemon:
    CYCLE_SECONDS: ClassVar[int] = 300
    UPGRADE_INTERVAL_SECONDS: ClassVar[int] = 86400

    @staticmethod
    async def cycle(debug: bool) -> HeadlessOutcome:
        async with await Repository.open(Repository.default_path()) as repo:
            return await HeadlessRunner.run(AppState.load(), repo, debug)

    @staticmethod
    def log(outcome: HeadlessOutcome) -> None:
        print(
            f"{datetime.now().astimezone().isoformat(timespec='seconds')} {HeadlessRunner.summary(outcome)}",
            flush=True,
        )

    @classmethod
    async def serve(cls, debug: bool) -> None:
        upgrade_due = time.monotonic() + cls.UPGRADE_INTERVAL_SECONDS
        while not SelfUpdater.is_stale():
            match await cls.cycle(debug):
                case HeadlessNothingToDo():
                    pass
                case outcome:
                    cls.log(outcome)
            if time.monotonic() < upgrade_due:
                await anyio.sleep(cls.CYCLE_SECONDS)
                continue
            await anyio.to_thread.run_sync(SelfUpdater.upgrade)
            upgrade_due = time.monotonic() + cls.UPGRADE_INTERVAL_SECONDS


class LaunchAgent:
    @staticmethod
    def is_supported() -> bool:
        return sys.platform == "darwin"

    @staticmethod
    def plist_path() -> Path:
        return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"

    @staticmethod
    def log_dir() -> Path:
        return Path.home() / ".cc-sentiment"

    @classmethod
    def stdout_log(cls) -> Path:
        return cls.log_dir() / "launchd.out"

    @classmethod
    def stderr_log(cls) -> Path:
        return cls.log_dir() / "launchd.err"

    @staticmethod
    def resolve_binary() -> Path:
        found = shutil.which("cc-sentiment")
        if found is None:
            raise RuntimeError(
                "cc-sentiment is not on PATH. Install it first with "
                "`uv tool install cc-sentiment`, then try again."
            )
        return Path(found)

    @classmethod
    def is_installed(cls) -> bool:
        return cls.plist_path().exists()

    @staticmethod
    def path_env(binary: Path) -> str:
        tools = [binary, *(Path(uv) for uv in [shutil.which("uv")] if uv is not None)]
        return ":".join(dict.fromkeys([*(str(tool.parent) for tool in tools), *SYSTEM_PATH]))

    @classmethod
    def render_plist(cls, binary: Path) -> bytes:
        return plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": [str(binary), "daemon"],
            "RunAtLoad": True,
            "KeepAlive": True,
            "ThrottleInterval": Daemon.CYCLE_SECONDS,
            "ProcessType": "Background",
            "LowPriorityIO": True,
            "StandardOutPath": str(cls.stdout_log()),
            "StandardErrorPath": str(cls.stderr_log()),
            "EnvironmentVariables": {"PATH": cls.path_env(binary)},
        })

    @classmethod
    def domain(cls) -> str:
        return f"gui/{os.getuid()}"

    @classmethod
    def install(cls) -> None:
        binary = cls.resolve_binary()
        path = cls.plist_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        cls.log_dir().mkdir(parents=True, exist_ok=True)
        path.write_bytes(cls.render_plist(binary))
        subprocess.run(
            ["launchctl", "bootout", cls.domain(), str(path)],
            capture_output=True, timeout=10,
        )
        subprocess.run(
            ["launchctl", "bootstrap", cls.domain(), str(path)],
            check=True, capture_output=True, timeout=10,
        )

    @classmethod
    def uninstall(cls) -> None:
        path = cls.plist_path()
        if path.exists():
            subprocess.run(
                ["launchctl", "bootout", cls.domain(), str(path)],
                capture_output=True, timeout=10,
            )
            path.unlink()
        cls.stdout_log().unlink(missing_ok=True)
        cls.stderr_log().unlink(missing_ok=True)
