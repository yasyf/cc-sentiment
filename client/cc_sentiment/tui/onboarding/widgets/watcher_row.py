from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.reactive import reactive

from cc_sentiment.tui.widgets.muted_line import MutedLine
from cc_sentiment.tui.widgets.pending_status import PendingStatus
from cc_sentiment.tui.widgets.section import Section


class WatcherRow(Section):
    DEFAULT_CSS: ClassVar[str] = """
    WatcherRow { height: auto; margin: 1 0 0 0; }
    WatcherRow > MutedLine#rate-limit-note { margin: 0; }
    """

    text: reactive[str] = reactive("")
    rate_limited: reactive[bool] = reactive(False)

    def __init__(
        self,
        label: str,
        *,
        rate_limit_text: str = "GitHub busy. Retrying.",
        id: str = "watcher-row",
    ) -> None:
        super().__init__(id=id)
        self._initial_label = label
        self._rate_limit_text = rate_limit_text
        self.set_reactive(WatcherRow.text, label)

    def compose(self) -> ComposeResult:
        self._spinner = PendingStatus(self._initial_label)
        self._note = MutedLine(self._rate_limit_text, id="rate-limit-note")
        self._note.display = False
        yield self._spinner
        yield self._note

    def watch_text(self, value: str) -> None:
        self._spinner.label = value

    def watch_rate_limited(self, value: bool) -> None:
        self._note.display = value
