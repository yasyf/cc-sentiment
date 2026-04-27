from __future__ import annotations

from textual import events, on
from textual.widgets import Static


class CommandBox(Static):
    DEFAULT_CSS = """
    CommandBox {
        width: 100%;
        height: 3;
        border: round $panel-lighten-2;
        padding: 0 1;
        color: $text;
        background: $boost;
    }
    CommandBox:hover { border: round $accent; background: $panel-lighten-1; }
    """

    COPIED_RESET_SECONDS = 1.5

    def __init__(self, command: str, **kwargs) -> None:
        super().__init__(self._format(command), **kwargs)
        self.command = command

    @staticmethod
    def _format(command: str, suffix: str = "") -> str:
        return f"[dim]$[/] {command}{suffix}"

    @on(events.Click)
    def on_click(self) -> None:
        self.app.copy_to_clipboard(self.command)
        self.update(self._format(self.command, "  [$success]copied[/]"))
        self.set_timer(self.COPIED_RESET_SECONDS, self._restore)

    def _restore(self) -> None:
        self.update(self._format(self.command))
