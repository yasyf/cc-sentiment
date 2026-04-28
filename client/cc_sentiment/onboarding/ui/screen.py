from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar, Generic, TypeVar

from textual import screen as t

from cc_sentiment.onboarding import State as GlobalState

from .state import BaseState

S = TypeVar("S", bound=BaseState)


class Screen(ABC, Generic[S]):
    State: ClassVar[type[S]]  # type: ignore[misc]

    def __init__(self) -> None:
        self.state: S = self.State.empty()

    @classmethod
    @abstractmethod
    def matcher(cls) -> GlobalState: ...

    @abstractmethod
    def render(self) -> t.Screen: ...
