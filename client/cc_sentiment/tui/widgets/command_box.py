from __future__ import annotations

from textual import events, on
from textual.widgets import Static


class CommandBox(Static):
    DEFAULT_CSS = """
    CommandBox {
        width: 100%;
        height: auto;
        border: round $panel-lighten-2;
        padding: 1 2;
        color: $text;
        background: $boost;

        &:hover { border: round $accent; background: $panel; }
    }
    """

    COPIED_RESET_SECONDS = 1.5

    def __init__(self, command: str, **kwargs) -> None:
        super().__init__(self.format(command), **kwargs)
        self.command = command

    @staticmethod
    def format(command: str, suffix: str = "") -> str:
        return f"[$text-muted]$[/] {command}{suffix}"

    @on(events.Click)
    def on_click(self) -> None:
        self.app.copy_to_clipboard(self.command)
        self.update(self.format(self.command, "  [$success]copied[/]"))
        self.set_timer(self.COPIED_RESET_SECONDS, self.restore)

    def restore(self) -> None:
        self.update(self.format(self.command))
