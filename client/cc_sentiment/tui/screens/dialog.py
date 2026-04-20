from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

T = TypeVar("T")


class Dialog(ModalScreen[T]):
    DEFAULT_CSS = """
    Dialog { align: center middle; background: $surface; }
    Dialog > #dialog-box {
        width: auto;
        max-width: 80;
        min-width: 50;
        height: auto;
        max-height: 24;
        border: heavy $accent;
        padding: 1 3;
        background: $panel;
    }
    Dialog > #dialog-box Button { margin: 1 1 0 0; }
    Dialog > #dialog-box .title { width: 100%; text-style: bold; color: $text; margin: 0 0 1 0; }
    Dialog > #dialog-box .detail { width: 100%; color: $text-muted; margin: 0 0 1 0; }
    Dialog > #dialog-box .emphasis { width: 100%; color: $text; margin: 0 0 2 0; }
    """
