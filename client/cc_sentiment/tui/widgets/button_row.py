from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Button


class ButtonRow(Horizontal):
    DEFAULT_CSS = """
    ButtonRow { width: 100%; height: auto; margin: 1 0 0 0; align-horizontal: left; }
    ButtonRow Button { margin: 0 1 0 0; }
    """

    def __init__(self, *buttons: Button, **kwargs) -> None:
        super().__init__(*buttons, **kwargs)
