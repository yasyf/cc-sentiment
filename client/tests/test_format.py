from __future__ import annotations

from cc_sentiment.tui.dashboard.format import ScoreEmoji, TimeFormat


def test_format_duration_under_30_seconds():
    assert TimeFormat.format_duration(0) == "a few seconds"
    assert TimeFormat.format_duration(29) == "a few seconds"


def test_format_duration_minutes():
    assert TimeFormat.format_duration(60) == "~1 min"
    assert TimeFormat.format_duration(900) == "~15 min"


def test_format_duration_hours():
    assert TimeFormat.format_duration(3600) == "~1 hour"
    assert TimeFormat.format_duration(7200) == "~2 hours"


def test_format_hour_short_matches_dashboard():
    assert TimeFormat.format_hour_short(0) == "12a"
    assert TimeFormat.format_hour_short(5) == "5a"
    assert TimeFormat.format_hour_short(12) == "12p"
    assert TimeFormat.format_hour_short(17) == "5p"
    assert TimeFormat.format_hour_short(23) == "11p"


def test_score_emoji_for_score_and_avg():
    assert ScoreEmoji.for_score(1) == "😡"
    assert ScoreEmoji.for_score(3) == "😐"
    assert ScoreEmoji.for_score(5) == "🤩"
    assert ScoreEmoji.for_avg(2.4) == "😒"
    assert ScoreEmoji.for_avg(4.6) == "🤩"
