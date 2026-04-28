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
    CardScreen Button.-primary:focus,
    CardScreen Button.-default:focus { text-style: bold; }
    CardScreen .muted { color: $text-muted; }
    CardScreen .success { color: $success; }
    CardScreen .warning { color: $warning; }
    CardScreen .error { color: $error; }
    CardScreen .code {
        background: $boost;
        border: round $panel-lighten-2;
        padding: 0 1;
    }
    """

    title: ClassVar[str] = ""

    def compose(self) -> ComposeResult:
        with Card(title=""):
            yield Title(self.title)
            yield from self.compose_card()

    @abstractmethod
    def compose_card(self) -> ComposeResult: ...
