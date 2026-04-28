from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from textual.screen import Screen as TextualScreen

from cc_sentiment.onboarding import Stage


class Screen(ABC):
    STAGE: ClassVar[Stage]
    State: ClassVar[type]

    @abstractmethod
    def screen(self) -> TextualScreen: ...
