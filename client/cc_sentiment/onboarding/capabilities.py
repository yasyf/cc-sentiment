from __future__ import annotations

import shutil
import subprocess
from functools import cached_property
from typing import ClassVar


class Capabilities:
    _instance: ClassVar[Capabilities | None] = None

    def __new__(cls) -> Capabilities:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @cached_property
    def has_ssh_keygen(self) -> bool:
        return self._has_tool("ssh-keygen")

    @cached_property
    def has_gpg(self) -> bool:
        return self._has_tool("gpg")

    @cached_property
    def has_gh(self) -> bool:
        return self._has_tool("gh")

    @cached_property
    def gh_authenticated(self) -> bool:
        if not self.has_gh:
            return False
        return subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=5,
        ).returncode == 0

    @cached_property
    def has_brew(self) -> bool:
        return self._has_tool("brew")

    @staticmethod
    def _has_tool(name: str) -> bool:
        return shutil.which(name) is not None
