from __future__ import annotations

import shutil
import subprocess
import sys
from importlib.metadata import version

from cc_sentiment.models import CLIENT_VERSION


class SelfUpdater:
    UV_TOOL_PATH_MARKER = "/uv/tools/cc-sentiment/"
    PACKAGE_NAME = "cc-sentiment"

    @classmethod
    def is_uv_tool_installed(cls) -> bool:
        return cls.UV_TOOL_PATH_MARKER in sys.executable

    @classmethod
    def upgrade_command(cls) -> list[str] | None:
        if not cls.is_uv_tool_installed():
            return None
        if (uv := shutil.which("uv")) is None:
            return None
        return [uv, "tool", "upgrade", cls.PACKAGE_NAME]

    @classmethod
    def maybe_upgrade(cls) -> None:
        if (command := cls.upgrade_command()) is None:
            return
        subprocess.Popen(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )

    @classmethod
    def upgrade(cls) -> None:
        if (command := cls.upgrade_command()) is None:
            return
        subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    @classmethod
    def is_stale(cls) -> bool:
        return version(cls.PACKAGE_NAME) != CLIENT_VERSION
