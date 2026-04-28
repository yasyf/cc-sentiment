from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Self


class BaseState(ABC):
    @classmethod
    @abstractmethod
    def empty(cls) -> Self: ...
