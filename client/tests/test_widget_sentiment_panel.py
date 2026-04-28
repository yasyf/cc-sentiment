from __future__ import annotations

from textual.app import App

from cc_sentiment.tui.dashboard.widgets import SentimentPanel
from tests.helpers import make_record


class SentimentPanelHarness(App[None]):
    def compose(self):
        yield SentimentPanel(id="panel")


async def test_sentiment_panel_empty_state():
    async with SentimentPanelHarness().run_test() as pilot:
        panel = pilot.app.query_one("#panel", SentimentPanel)
        panel.update_from_records([])
        await pilot.pause()
        assert "warming up" in str(panel.content)


async def test_sentiment_panel_renders_histogram():
    records = [
        make_record(score=1), make_record(score=1), make_record(score=2),
        make_record(score=3), make_record(score=3), make_record(score=3),
        make_record(score=4), make_record(score=4), make_record(score=5),
    ]
    async with SentimentPanelHarness().run_test() as pilot:
        panel = pilot.app.query_one("#panel", SentimentPanel)
        panel.update_from_records(records)
        await pilot.pause()
        body = str(panel.content)
        assert "frustrated" in body
        assert "chats" in body
        assert "😡" in body
        assert "🤩" in body
        assert "[$success]" in body
        assert "[$error]" in body
