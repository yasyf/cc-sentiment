from __future__ import annotations

from unittest.mock import patch

from textual.app import App
from textual.containers import Vertical
from textual.widgets import Static

from cc_sentiment.tui.moments_view import MomentsView


class MomentsHarness(App[None]):
    def compose(self):
        with Vertical(id="section"):
            yield Static("", id="log")


async def test_moments_view_snippet_survives_bracket_heavy_content():
    with patch("cc_sentiment.tui.moments_view.random.random", return_value=0.0):
        async with MomentsHarness().run_test() as pilot:
            moments = MomentsView(
                app=pilot.app,
                section=pilot.app.query_one("#section"),
                log=pilot.app.query_one("#log", Static),
            )
            moments.show()
            await moments.add_snippet(
                "2026-04-03T11:14:13.287367+0000 +13m26s [🐞][DSPyCompilationServer.compile] 'ignore'",
                1,
            )
            moments.last_snippet_at = 0.0
            await moments.add_snippet("prefix text [dim", 1)
            moments.last_snippet_at = 0.0
            await moments.add_snippet("<task-notification> <task-id>abc</task-id> body", 5)
            await pilot.pause()
            assert len(moments.lines) >= 1
