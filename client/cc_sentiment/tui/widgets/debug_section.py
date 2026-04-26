from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.widgets import Static

from cc_sentiment.tui.progress import DebugState
from cc_sentiment.tui.widgets.card import Card


class DebugSection(Card):
    DEFAULT_CSS: ClassVar[str] = """
    DebugSection > #debug-body { color: $text-muted; height: auto; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(title="debug", **kwargs)

    def compose(self) -> ComposeResult:
        yield Static("", id="debug-body")

    def render_state(self, s: DebugState) -> None:
        stopped = f"  stopped={s.card_stopped}" if s.card_stopped else ""
        retry = (
            f"  retry in {max(0, int(round(s.card_next_retry - s.card_elapsed)))}s"
            if s.card_next_retry is not None
            and s.card_stopped is None
            and s.card_next_retry > s.card_elapsed
            else ""
        )
        lines = [
            f"engine:   [b]{s.engine_name}[/]",
            f"nlp:      {s.nlp_state}",
            *([f"nlp out:  {s.nlp_output}"] if s.nlp_output else []),
            f"prewarm:  model={s.prewarm_model}",
            f"card:     attempts={s.card_attempts}  status=[b]{s.card_last_status}[/]  "
            f"elapsed={s.card_elapsed:.0f}s{retry}{stopped}",
            f"share:    {s.share_state}",
        ]
        self.query_one("#debug-body", Static).update("\n".join(lines))
