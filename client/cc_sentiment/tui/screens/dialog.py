from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

T = TypeVar("T")


class Dialog(ModalScreen[T]):
    DEFAULT_CSS = """
    Dialog { align: center middle; background: transparent; }
    Dialog > #dialog-box {
        width: auto;
        max-width: 60;
        min-width: 40;
        height: auto;
        border: heavy $accent;
        padding: 1 2;
        background: $panel;
    }
    Dialog > #dialog-box Button { margin: 1 1 0 0; }
    Dialog > #dialog-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    Dialog > #dialog-box .detail { color: $text-muted; margin: 0 0 1 0; }
    Dialog > #dialog-box .emphasis { color: $text; margin: 0 0 2 0; }
    """
