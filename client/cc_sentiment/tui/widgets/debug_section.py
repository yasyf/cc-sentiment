from __future__ import annotations

from textual.app import ComposeResult
from textual.widgets import Static

from cc_sentiment.tui.progress import DebugState
from cc_sentiment.tui.widgets.section import Section


class DebugSection(Section):
    DEFAULT_CSS = """
    DebugSection { border: round $surface; }
    DebugSection > #debug-body { color: $text-muted; height: auto; }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(title="debug", **kwargs)

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
        ]
        self.query_one("#debug-body", Static).update("\n".join(lines))
