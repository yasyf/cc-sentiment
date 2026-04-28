from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import ClassVar

import anyio


@dataclass(frozen=True, slots=True)
class Capabilities:
    has_ssh_keygen: bool
    has_gpg: bool
    has_gh: bool
    gh_authenticated: bool
    has_brew: bool

    _instance: ClassVar[Capabilities | None] = None
    _lock: ClassVar[anyio.Lock] = anyio.Lock()

    @classmethod
    async def get(cls) -> Capabilities:
        if cls._instance is not None:
            return cls._instance
        async with cls._lock:
            if cls._instance is not None:
                return cls._instance
            cls._instance = await cls._build()
            return cls._instance

    @classmethod
    async def invalidate(cls) -> None:
        async with cls._lock:
            cls._instance = None

    @classmethod
    async def _build(cls) -> Capabilities:
        from cc_sentiment.signing import KeyDiscovery

        has_gh = shutil.which("gh") is not None
        instance = cls(
            has_ssh_keygen=shutil.which("ssh-keygen") is not None,
            has_gpg=shutil.which("gpg") is not None,
            has_gh=has_gh,
            gh_authenticated=await KeyDiscovery.gh_authenticated() if has_gh else False,
            has_brew=shutil.which("brew") is not None,
        )
        return instance
