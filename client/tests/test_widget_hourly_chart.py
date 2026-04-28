from __future__ import annotations

from datetime import datetime, timedelta, timezone

from textual.app import App

from cc_sentiment.tui.widgets import HourlyChart
from tests.helpers import make_record


class ChartHarness(App[None]):
    def compose(self):
        yield HourlyChart(id="chart")


async def test_hourly_chart_renders_volume_row_and_axis():
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=5, time=base + timedelta(hours=8)),
        make_record(score=1, time=base + timedelta(hours=14)),
        make_record(score=3, time=base + timedelta(hours=20)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        lines = str(chart.content).split("\n")
        assert len(lines) == 5
        bar_row = lines[2]
        assert "[$success]" in bar_row
        assert "[$error]" in bar_row
        assert "[$warning]" in bar_row
        assert "─" * 24 in lines[3]
        labels = lines[4]
        assert "12a" in labels
        assert "6a" in labels
        assert "12p" in labels
        assert "6p" in labels


async def test_hourly_chart_colors_track_avg_sentiment():
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=1, time=base + timedelta(hours=8)),
        make_record(score=2, time=base + timedelta(hours=8, minutes=1)),
        make_record(score=1, time=base + timedelta(hours=9)),
        make_record(score=4, time=base + timedelta(hours=10)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        bar_row = str(chart.content).split("\n")[2]
        assert "[$error]" in bar_row
        assert "[$success]" in bar_row


async def test_hourly_chart_drops_records_older_than_window():
    old = datetime.now(timezone.utc) - timedelta(days=HourlyChart.WINDOW_DAYS + 1)
    records = [
        make_record(score=1, time=old),
        make_record(score=1, time=old + timedelta(hours=1)),
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        assert "no data yet" in str(chart.content)


async def test_hourly_chart_empty_records():
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart([])
        await pilot.pause()
        assert "no data yet" in str(chart.content)


async def test_hourly_chart_caption_calls_out_tough_hour():
    base = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    records = [
        make_record(score=1, time=base + timedelta(hours=17, minutes=i))
        for i in range(HourlyChart.UCB_MIN_SAMPLES)
    ]
    async with ChartHarness().run_test() as pilot:
        chart = pilot.app.query_one("#chart", HourlyChart)
        chart.update_chart(records)
        await pilot.pause()
        caption = str(chart.content).split("\n")[0]
        assert "tough hour" in caption
