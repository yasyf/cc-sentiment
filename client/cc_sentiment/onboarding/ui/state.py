from __future__ import annotations

from abc import ABC
from typing import Self


class BaseState(ABC):
    @classmethod
    def empty(cls) -> Self:
        return cls()
