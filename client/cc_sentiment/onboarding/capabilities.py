from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from dataclasses import dataclass, field

import anyio
import anyio.to_thread


@dataclass(frozen=True, slots=True)
class Capabilities:
    has_ssh_keygen: bool
    has_gpg: bool
    has_gh: bool
    gh_authenticated: bool
    has_brew: bool
    can_clipboard: bool
    can_open_browser: bool


class CapabilityProbe:
    @classmethod
    async def detect(cls) -> Capabilities:
        ssh, gpg, gh, brew = await cls._probe_tools()
        return Capabilities(
            has_ssh_keygen=ssh,
            has_gpg=gpg,
            has_gh=gh,
            gh_authenticated=(
                await anyio.to_thread.run_sync(cls._gh_authenticated) if gh else False
            ),
            has_brew=brew,
            can_clipboard=await anyio.to_thread.run_sync(cls._has_clipboard),
            can_open_browser=await anyio.to_thread.run_sync(cls._has_browser),
        )

    @classmethod
    async def _probe_tools(cls) -> tuple[bool, bool, bool, bool]:
        results: dict[str, bool] = {}

        async def probe(name: str) -> None:
            results[name] = await anyio.to_thread.run_sync(cls._has_tool, name)

        async with anyio.create_task_group() as tg:
            for tool in ("ssh-keygen", "gpg", "gh", "brew"):
                tg.start_soon(probe, tool)

        return results["ssh-keygen"], results["gpg"], results["gh"], results["brew"]

    @staticmethod
    def _has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def _gh_authenticated() -> bool:
        result = subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0

    @staticmethod
    def _has_clipboard() -> bool:
        match sys.platform:
            case "darwin":
                return shutil.which("pbcopy") is not None
            case "win32":
                return shutil.which("clip") is not None
            case _:
                return any(
                    shutil.which(t) is not None
                    for t in ("wl-copy", "xclip", "xsel")
                )

    @staticmethod
    def _has_browser() -> bool:
        try:
            return webbrowser.get() is not None
        except webbrowser.Error:
            return False


@dataclass(slots=True)
class CapabilityCache:
    cached: Capabilities | None = None
    lock: anyio.Lock = field(default_factory=anyio.Lock)

    async def get(self) -> Capabilities:
        async with self.lock:
            if self.cached is None:
                self.cached = await CapabilityProbe.detect()
            return self.cached

    def invalidate(self) -> None:
        self.cached = None
