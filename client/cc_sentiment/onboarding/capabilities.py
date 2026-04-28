from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser
from collections.abc import Awaitable, Callable
from typing import Any, ClassVar

import anyio
import anyio.to_thread


class async_cached_property:
    def __init__(self, func: Callable[[Any], Awaitable[Any]]) -> None:
        self.func = func
        self.name = func.__name__

    def __set_name__(self, owner: type, name: str) -> None:
        self.name = name

    def __get__(self, instance: Any, owner: type | None = None) -> Any:
        if instance is None:
            return self
        return self._fetch(instance)

    async def _fetch(self, instance: Any) -> Any:
        cache = instance.__dict__.setdefault("_acache", {})
        if self.name in cache:
            return cache[self.name]
        locks = instance.__dict__.setdefault("_alocks", {})
        async with locks.setdefault(self.name, anyio.Lock()):
            if self.name not in cache:
                cache[self.name] = await self.func(instance)
            return cache[self.name]


class Capabilities:
    _instance: ClassVar[Capabilities | None] = None

    def __new__(cls) -> Capabilities:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    @classmethod
    def seed(cls, **values: bool) -> None:
        cls().__dict__.setdefault("_acache", {}).update(values)

    @async_cached_property
    async def has_ssh_keygen(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_tool, "ssh-keygen")

    @async_cached_property
    async def has_gpg(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_tool, "gpg")

    @async_cached_property
    async def has_gh(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_tool, "gh")

    @async_cached_property
    async def gh_authenticated(self) -> bool:
        if not await self.has_gh:
            return False
        return await anyio.to_thread.run_sync(self._gh_authenticated)

    @async_cached_property
    async def has_brew(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_tool, "brew")

    @async_cached_property
    async def can_clipboard(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_clipboard)

    @async_cached_property
    async def can_open_browser(self) -> bool:
        return await anyio.to_thread.run_sync(self._has_browser)

    @staticmethod
    def _has_tool(name: str) -> bool:
        return shutil.which(name) is not None

    @staticmethod
    def _gh_authenticated() -> bool:
        return subprocess.run(
            ["gh", "auth", "status"],
            capture_output=True, text=True, timeout=5,
        ).returncode == 0

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
