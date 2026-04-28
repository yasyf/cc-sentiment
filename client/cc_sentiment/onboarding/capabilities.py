from __future__ import annotations

import shutil
from typing import ClassVar

import anyio
import anyio.to_thread
from asyncstdlib.functools import AwaitableValue, cached_property


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
        instance = cls()
        for name, value in values.items():
            instance.__dict__[name] = AwaitableValue(value)

    @cached_property
    async def has_ssh_keygen(self) -> bool:
        return await anyio.to_thread.run_sync(self._which, "ssh-keygen")

    @cached_property
    async def has_gpg(self) -> bool:
        return await anyio.to_thread.run_sync(self._which, "gpg")

    @cached_property
    async def has_gh(self) -> bool:
        return await anyio.to_thread.run_sync(self._which, "gh")

    @cached_property
    async def gh_authenticated(self) -> bool:
        if not await self.has_gh:
            return False
        with anyio.fail_after(5):
            result = await anyio.run_process(["gh", "auth", "status"], check=False)
        return result.returncode == 0

    @cached_property
    async def has_brew(self) -> bool:
        return await anyio.to_thread.run_sync(self._which, "brew")

    @staticmethod
    def _which(name: str) -> bool:
        return shutil.which(name) is not None
