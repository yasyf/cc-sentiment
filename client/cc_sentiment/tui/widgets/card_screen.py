from __future__ import annotations

from abc import abstractmethod
from typing import ClassVar, TypeVar

from textual.app import ComposeResult
from textual.screen import ModalScreen

from cc_sentiment.tui.widgets.card import Card
from cc_sentiment.tui.widgets.title import Title

T = TypeVar("T")


class CardScreen(ModalScreen[T]):
    DEFAULT_CSS: ClassVar[str] = """
    CardScreen {
        layout: vertical;
        align: center middle;
        background: $background 60%;
    }
    CardScreen > Card {
        width: 80%;
        max-width: 90;
        min-width: 50;
        height: auto;
        max-height: 90%;
        overflow-y: auto;
        background: $panel;
        padding: 2 3;
    }
    """

    title: ClassVar[str] = ""

    def compose(self) -> ComposeResult:
        with Card(title=""):
            yield Title(self.title)
            yield from self.compose_card()

    @abstractmethod
    def compose_card(self) -> ComposeResult: ...
