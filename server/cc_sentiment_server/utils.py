from __future__ import annotations

__all__ = ["noop"]


async def noop(*args: object, **kwargs: object) -> None:
    pass
