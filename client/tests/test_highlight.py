from __future__ import annotations

import pytest
from cc_transcript.sentiment.lexicon import tokenize

from cc_sentiment.highlight import (
    Highlighter,
    HighlightSpan,
    WindowedSlice,
    tokenize_spans,
)


@pytest.mark.parametrize(
    "text",
    [
        "STOP GUESSING",
        "this seems incorrect for tqdm",
        "strict=True))",
        "wtf this is broken and shitty",
        "LOST losing can't",
        "Grüße ÜBER die straße",
        "café Ελλάς 你好 world",
    ],
)
def test_tokenize_spans_matches_upstream_tokenize(text):
    spans = tokenize_spans(text)
    assert [tok.lower for tok in spans] == tokenize(text)
    assert all(text[tok.start : tok.end].lower() == tok.lower for tok in spans)


def test_profanity_tokens_in_finds_inflections():
    assert Highlighter.profanity_tokens_in("shit, that broke") == ["shit"]
    assert Highlighter.profanity_tokens_in("FUCKING broken AGAIN") == ["fucking"]
    assert Highlighter.profanity_tokens_in("totally fine here") == []


def test_profanity_tokens_in_collects_all_matches():
    matches = Highlighter.profanity_tokens_in("damn it, this is shit and bullshit too")
    assert matches == ["damn", "shit", "bullshit"]


def test_slice_window_both_ellipses_center():
    full = "a" * 30 + "BUG" + "b" * 30
    anchor = HighlightSpan(start=30, end=33, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=20)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.body.endswith("…")
    assert "BUG" in slice_.body
    assert len(slice_.body) == 20


def test_slice_window_drops_leading_near_start():
    full = "bug " + "a" * 100
    anchor = HighlightSpan(start=0, end=3, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=20)
    assert not slice_.leading
    assert not slice_.body.startswith("…")
    assert slice_.body.endswith("…")
    assert slice_.body.startswith("bug")
    assert len(slice_.body) == 20


def test_slice_window_drops_trailing_near_end():
    full = "a" * 100 + " bug"
    anchor = HighlightSpan(start=101, end=104, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=20)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.body.endswith("bug")
    assert len(slice_.body) == 20


def test_slice_window_returns_full_when_short():
    full = "short text with bug"
    anchor = HighlightSpan(start=16, end=19, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=60)
    assert slice_.body == full
    assert slice_.full_offset == 0
    assert not slice_.leading


def test_apply_styles_translates_indices_into_body():
    slice_ = WindowedSlice(body="…abc bug def…", full_offset=30, kept_len=11, leading=True)
    candidates = [HighlightSpan(start=34, end=37, color="red", priority=2)]
    text = Highlighter.apply_styles(slice_, candidates)
    assert any(
        str(s.style) == "red" and (s.start, s.end) == (5, 8)
        for s in text.spans
    )


def test_apply_styles_drops_out_of_window_candidates():
    slice_ = WindowedSlice(body="…abc bug def…", full_offset=30, kept_len=11, leading=True)
    candidates = [HighlightSpan(start=100, end=103, color="green", priority=2)]
    text = Highlighter.apply_styles(slice_, candidates)
    assert not list(text.spans)


def test_apply_styles_skips_empty_color():
    slice_ = WindowedSlice(body="hello", full_offset=0, kept_len=5, leading=False)
    candidates = [HighlightSpan(start=0, end=5, color="", priority=1)]
    text = Highlighter.apply_styles(slice_, candidates)
    assert not list(text.spans)


def test_collect_candidates_tags_profanity_and_lemmas():
    full = "this is perfect but the bug is broken"
    candidates = Highlighter.collect_candidates(full, score=3)
    colors = {(c.start, c.color) for c in candidates}
    assert (8, "green") in colors
    assert (24, "red") in colors
    assert (31, "red") in colors


def test_collect_candidates_tone_gates_positive_words_in_negative_messages():
    full = "fix it perfect"
    candidates = Highlighter.collect_candidates(full, score=1)
    colors = {(c.start, c.color) for c in candidates}
    assert (0, "green") not in colors
    assert (7, "green") not in colors


def test_collect_candidates_tags_strong_negative_red():
    full = "you are an idiot"
    candidates = Highlighter.collect_candidates(full, score=1)
    colors = {(c.start, c.color) for c in candidates}
    assert (11, "red") in colors


def test_message_polarity_counts_strong_negative():
    assert Highlighter.message_polarity("you are an idiot") <= -3


def test_collect_candidates_catches_frustration_pattern_for_low_scores():
    full = "wtf is happening here, completely useless"
    candidates = Highlighter.collect_candidates(full, score=1)
    assert any(c.priority == 3 and c.color == "red" for c in candidates)


def test_collect_candidates_tags_expanded_profanity():
    full = "this is fucking broken and shitty"
    candidates = Highlighter.collect_candidates(full, score=2)
    profanity_starts = {c.start for c in candidates if c.priority == 3}
    assert 8 in profanity_starts
    assert 27 in profanity_starts


def test_fallback_anchor_picks_longest_content_word():
    anchor = Highlighter.fallback_anchor("keep monitoring it goes")
    assert anchor is not None
    assert (anchor.start, anchor.end) == (5, 15)
    assert anchor.color == ""
    assert anchor.priority == 1


def test_fallback_anchor_never_colors_by_score():
    anchor = Highlighter.fallback_anchor("thing")
    assert anchor is not None
    assert anchor.color == ""


def test_fallback_anchor_returns_none_when_no_eligible_token():
    assert Highlighter.fallback_anchor("is a 42") is None


def test_windowed_highlight_applies_frustration_regex():
    text = Highlighter.windowed_highlight("wtf this is broken", score=2)
    assert any(
        str(s.style) == "red" and s.start == 0 and s.end == 3
        for s in text.spans
    )


def test_windowed_highlight_truncates_long_neutral_text():
    long = "x" * 200
    text = Highlighter.windowed_highlight(long, score=4)
    assert len(text.plain) == Highlighter.MAX_SNIPPET_CHARS
    assert text.plain.endswith("…")


def test_windowed_highlight_anchors_on_profanity_past_prefix():
    prefix = (
        "neutral filler text that says nothing special just padding "
        "here too here still more filler going on and on and on "
    )
    assert len(prefix) > Highlighter.MAX_SNIPPET_CHARS
    full = prefix + "fuck this"
    text = Highlighter.windowed_highlight(full, score=1)
    assert "fuck" in text.plain
    assert any(str(s.style) == "red" for s in text.spans)


def test_windowed_highlight_leaves_neutral_message_uncolored():
    full = (
        "keep monitoring it as it goes and give me an updated ETA for "
        "the server deployment so we can plan the rest of the launch"
    )
    for score in (1, 2, 3, 4, 5):
        text = Highlighter.windowed_highlight(full, score=score)
        assert not list(text.spans), f"score={score} produced spans {list(text.spans)}"


def test_windowed_highlight_colors_stop_red():
    text = Highlighter.windowed_highlight("STOP GUESSING", score=3)
    assert any(str(s.style) == "red" for s in text.spans)
    assert not any(str(s.style) == "green" for s in text.spans)


def test_windowed_highlight_colors_continue_green():
    text = Highlighter.windowed_highlight("Continue from where you left off.", score=3)
    assert any(str(s.style) == "green" for s in text.spans)
    assert not any(str(s.style) == "red" for s in text.spans)


def test_slice_window_snaps_leading_to_word_boundary():
    full = "alpha bravo charlie delta BUG echo foxtrot golf hotel india"
    anchor = HighlightSpan(start=full.index("BUG"), end=full.index("BUG") + 3, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=30)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.full_offset == 0 or full[slice_.full_offset - 1].isspace()
    assert "BUG" in slice_.body


def test_slice_window_snaps_trailing_to_word_boundary():
    full = "alpha bravo charlie delta BUG echo foxtrot golf hotel india"
    anchor = HighlightSpan(start=full.index("BUG"), end=full.index("BUG") + 3, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=30)
    assert slice_.body.endswith("…")
    tail = slice_.full_offset + slice_.kept_len
    assert tail == len(full) or full[tail].isspace() or full[tail - 1].isspace()
    assert "BUG" in slice_.body


def test_slice_window_keeps_char_cut_when_no_whitespace_nearby():
    full = "a" * 40 + "BUG" + "a" * 40
    anchor = HighlightSpan(start=40, end=43, color="red", priority=2)
    slice_ = Highlighter.slice_window(full, anchor, width=20)
    assert slice_.leading
    assert slice_.body.startswith("…")
    assert slice_.body.endswith("…")
    assert "BUG" in slice_.body
    assert len(slice_.body) == 20


def test_collect_candidates_highlights_afinn_words_without_pos():
    full = "the progress is tracked at the top"
    candidates = Highlighter.collect_candidates(full, score=4)
    colors = {(c.start, c.color) for c in candidates}
    assert (4, "green") in colors
    assert (31, "green") in colors


def test_collect_candidates_tags_curated_domain_words():
    full = "this bug is a nightmare"
    candidates = Highlighter.collect_candidates(full, score=2)
    starts = {c.start for c in candidates if c.color == "red"}
    assert 5 in starts
    assert 14 in starts


def test_windowed_highlight_colors_incorrect_red():
    text = Highlighter.windowed_highlight("this seems incorrect for tqdm", score=3)
    assert any(str(s.style) == "red" for s in text.spans)


def test_collect_candidates_skips_python_literal_true():
    full = "strict=True))"
    candidates = Highlighter.collect_candidates(full, score=2)
    assert not any(c.start == 7 for c in candidates)


def test_collect_candidates_skips_kwarg_value_after_equals():
    full = "verbose=working"
    candidates = Highlighter.collect_candidates(full, score=4)
    assert not any(c.start == 8 for c in candidates)


def test_collect_candidates_keeps_lowercase_true_as_word():
    full = "this is true love"
    candidates = Highlighter.collect_candidates(full, score=4)
    assert any(c.start == 8 and c.color == "green" for c in candidates)


def test_collect_candidates_tags_incorrect_red_from_override():
    full = "this seems incorrect for tqdm"
    candidates = Highlighter.collect_candidates(full, score=3)
    assert any(c.start == 11 and c.color == "red" for c in candidates)
