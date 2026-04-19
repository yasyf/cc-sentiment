from __future__ import annotations

from typing import ClassVar

from textual.app import ComposeResult
from textual.widgets import Static

from cc_sentiment.tui.progress import DebugState
from cc_sentiment.tui.widgets.section import Section


class DebugSection(Section):
    BORDER_SUBTITLE: ClassVar[str] = "debug"
    DEFAULT_CSS: ClassVar[str] = """
    DebugSection { border: round $surface; }
    DebugSection > #debug-body { color: $text-muted; height: auto; }
    """

    def compose(self) -> ComposeResult:
        yield Static("", id="debug-body")

    def render_state(self, s: DebugState) -> None:
        stopped = f"  stopped={s.card_stopped}" if s.card_stopped else ""
        lines = [
            f"engine:   [b]{s.engine_name}[/]",
            f"nlp:      {s.nlp_state}",
            f"prewarm:  uvx={s.prewarm_uvx}  model={s.prewarm_model}",
            f"card:     attempts={s.card_attempts}  status=[b]{s.card_last_status}[/]  "
            f"elapsed={s.card_elapsed:.0f}s{stopped}",
            f"share:    prewarm={s.share_prewarm}",
        ]
        self.query_one("#debug-body", Static).update("\n".join(lines))
