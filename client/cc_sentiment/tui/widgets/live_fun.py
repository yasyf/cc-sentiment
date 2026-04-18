from __future__ import annotations

from textual.widgets import Static

from cc_sentiment.tui.progress import LiveFunStats


class LiveFunBox(Static):
    DEFAULT_CSS = "LiveFunBox { height: 1; color: $text-muted; margin: 1 0 0 0; }"

    def render_stats(self, stats: LiveFunStats) -> None:
        match stats.top():
            case None:
                self.update("[dim]No swearing yet — you're kind to Claude.[/]")
            case (word, count):
                self.update(f'[dim]Top venting:[/] [b]"{word}"[/] ×{count}')
