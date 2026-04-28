from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from textual.widget import Widget

from cc_sentiment.onboarding import Stage


class Screen(ABC):
    STAGE: ClassVar[Stage]
    State: ClassVar[type]

    @abstractmethod
    def screen(self) -> Widget: ...
