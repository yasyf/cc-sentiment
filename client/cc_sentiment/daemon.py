from __future__ import annotations

import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

LABEL = "cc.sentiments.agent"
RUN_INTERVAL_SECONDS = 86400
PATH_ENV = "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"


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

    @classmethod
    def render_plist(cls, binary: Path) -> bytes:
        return plistlib.dumps({
            "Label": LABEL,
            "ProgramArguments": [str(binary), "run"],
            "StartInterval": RUN_INTERVAL_SECONDS,
            "StandardOutPath": str(cls.stdout_log()),
            "StandardErrorPath": str(cls.stderr_log()),
            "EnvironmentVariables": {"PATH": PATH_ENV},
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
