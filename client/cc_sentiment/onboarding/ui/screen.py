from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from textual import screen as t

from cc_sentiment.onboarding import Stage

S = TypeVar("S")


class Screen(ABC, Generic[S]):
    State: ClassVar[type[S]]  # type: ignore[misc]

    @classmethod
    @abstractmethod
    def matcher(cls) -> Stage: ...

    @abstractmethod
    def render(self) -> t.Screen: ...
