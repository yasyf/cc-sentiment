from __future__ import annotations

import shutil
import subprocess
import sys


class SelfUpdater:
    UV_TOOL_PATH_MARKER = "/uv/tools/cc-sentiment/"
    PACKAGE_NAME = "cc-sentiment"

    @classmethod
    def is_uv_tool_installed(cls) -> bool:
        return cls.UV_TOOL_PATH_MARKER in sys.executable

    @classmethod
    def maybe_upgrade(cls) -> None:
        if not cls.is_uv_tool_installed():
            return
        if (uv := shutil.which("uv")) is None:
            return
        subprocess.Popen(
            [uv, "tool", "upgrade", cls.PACKAGE_NAME],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
