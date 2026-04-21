from __future__ import annotations

from dataclasses import dataclass

import pytest

from cc_sentiment.highlight import Highlighter, HighlightSpan, WindowedSlice


@dataclass
class FakeToken:
    idx: int
    text: str
    pos_: str
    lemma_: str


@pytest.fixture
def real_nlp(monkeypatch):
    spacy = pytest.importorskip("spacy")
    try:
        model = spacy.load("en_core_web_sm", disable=["parser"])
    except OSError:
        pytest.skip("spaCy en_core_web_sm not available")
    monkeypatch.setattr("cc_sentiment.nlp.NLP.model", model)
    return model


@pytest.fixture
def real_lexicon(monkeypatch):
    from afinn import Afinn
    monkeypatch.setattr(
        "cc_sentiment.lexicon.Lexicon.afinn",
        Afinn(language="en", emoticons=False),
    )


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


def test_collect_candidates_tags_profanity_and_lemmas(real_lexicon):
    full = "this is perfect but the bug is broken"
    tokens = [
        FakeToken(idx=0, text="this", pos_="PRON", lemma_="this"),
        FakeToken(idx=5, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=8, text="perfect", pos_="ADJ", lemma_="perfect"),
        FakeToken(idx=16, text="but", pos_="CCONJ", lemma_="but"),
        FakeToken(idx=20, text="the", pos_="DET", lemma_="the"),
        FakeToken(idx=24, text="bug", pos_="NOUN", lemma_="bug"),
        FakeToken(idx=28, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=31, text="broken", pos_="ADJ", lemma_="broken"),
    ]
    candidates = Highlighter.collect_candidates(full, tokens, score=2)
    colors = {(c.start, c.color) for c in candidates}
    assert (8, "green") in colors
    assert (24, "red") in colors
    assert (31, "red") in colors


def test_collect_candidates_catches_frustration_pattern_for_low_scores():
    full = "wtf is happening here, completely useless"
    candidates = Highlighter.collect_candidates(full, [], score=1)
    assert any(c.priority == 3 and c.color == "red" for c in candidates)


def test_collect_candidates_tags_expanded_profanity(real_lexicon):
    full = "this is fucking broken and shitty"
    tokens = [
        FakeToken(idx=0, text="this", pos_="PRON", lemma_="this"),
        FakeToken(idx=5, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=8, text="fucking", pos_="ADJ", lemma_="fucking"),
        FakeToken(idx=16, text="broken", pos_="ADJ", lemma_="broken"),
        FakeToken(idx=23, text="and", pos_="CCONJ", lemma_="and"),
        FakeToken(idx=27, text="shitty", pos_="ADJ", lemma_="shitty"),
    ]
    candidates = Highlighter.collect_candidates(full, tokens, score=2)
    profanity_starts = {c.start for c in candidates if c.priority == 3}
    assert 8 in profanity_starts
    assert 27 in profanity_starts


def test_fallback_anchor_picks_longest_content_word():
    tokens = [
        FakeToken(idx=0, text="keep", pos_="VERB", lemma_="keep"),
        FakeToken(idx=5, text="monitoring", pos_="VERB", lemma_="monitor"),
        FakeToken(idx=16, text="it", pos_="PRON", lemma_="it"),
        FakeToken(idx=19, text="goes", pos_="VERB", lemma_="go"),
    ]
    anchor = Highlighter.fallback_anchor(tokens)
    assert anchor is not None
    assert (anchor.start, anchor.end) == (5, 15)
    assert anchor.color == ""
    assert anchor.priority == 1


def test_fallback_anchor_never_colors_by_score():
    tokens = [FakeToken(idx=0, text="thing", pos_="NOUN", lemma_="thing")]
    anchor = Highlighter.fallback_anchor(tokens)
    assert anchor is not None
    assert anchor.color == ""


def test_fallback_anchor_returns_none_when_no_eligible_token():
    tokens = [
        FakeToken(idx=0, text="is", pos_="VERB", lemma_="be"),
        FakeToken(idx=3, text="a", pos_="DET", lemma_="a"),
        FakeToken(idx=5, text="42", pos_="NUM", lemma_="42"),
    ]
    assert Highlighter.fallback_anchor(tokens) is None


def test_windowed_highlight_prefix_fallback_applies_frustration_regex():
    text = Highlighter.windowed_highlight("wtf this is broken", score=2)
    assert any(
        str(s.style) == "red" and s.start == 0 and s.end == 3
        for s in text.spans
    )


def test_windowed_highlight_prefix_fallback_truncates_when_no_nlp():
    long = "x" * 200
    text = Highlighter.windowed_highlight(long, score=4)
    assert len(text.plain) == Highlighter.MAX_SNIPPET_CHARS
    assert text.plain.endswith("…")


def test_windowed_highlight_anchors_on_profanity_past_prefix(real_nlp, real_lexicon):
    prefix = (
        "neutral filler text that says nothing special just padding "
        "here too here still more filler going on and on and on "
    )
    assert len(prefix) > Highlighter.MAX_SNIPPET_CHARS
    full = prefix + "fuck this"
    text = Highlighter.windowed_highlight(full, score=1)
    assert "fuck" in text.plain
    assert any(str(s.style) == "red" for s in text.spans)


def test_windowed_highlight_leaves_neutral_message_uncolored(real_nlp, real_lexicon):
    full = (
        "keep monitoring it as it goes and give me an updated ETA for "
        "the server deployment so we can plan the rest of the launch"
    )
    for score in (1, 2, 3, 4, 5):
        text = Highlighter.windowed_highlight(full, score=score)
        assert not list(text.spans), f"score={score} produced spans {list(text.spans)}"


def test_windowed_highlight_colors_stop_red_even_in_positive_bucket(real_nlp, real_lexicon):
    text = Highlighter.windowed_highlight("STOP GUESSING", score=4)
    assert any(str(s.style) == "red" for s in text.spans)
    assert not any(str(s.style) == "green" for s in text.spans)


def test_windowed_highlight_colors_continue_green_even_in_negative_bucket(real_nlp, real_lexicon):
    text = Highlighter.windowed_highlight("Continue from where you left off.", score=2)
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


def test_collect_candidates_skips_generic_afinn_noun(real_lexicon):
    full = "the progress is tracked at the top"
    tokens = [
        FakeToken(idx=0, text="the", pos_="DET", lemma_="the"),
        FakeToken(idx=4, text="progress", pos_="NOUN", lemma_="progress"),
        FakeToken(idx=13, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=16, text="tracked", pos_="VERB", lemma_="track"),
        FakeToken(idx=24, text="at", pos_="ADP", lemma_="at"),
        FakeToken(idx=27, text="the", pos_="DET", lemma_="the"),
        FakeToken(idx=31, text="top", pos_="NOUN", lemma_="top"),
    ]
    candidates = Highlighter.collect_candidates(full, tokens, score=4)
    assert not any(c.start == 4 for c in candidates)
    assert not any(c.start == 31 for c in candidates)


def test_collect_candidates_keeps_curated_noun(real_lexicon):
    full = "this bug is a nightmare"
    tokens = [
        FakeToken(idx=0, text="this", pos_="PRON", lemma_="this"),
        FakeToken(idx=5, text="bug", pos_="NOUN", lemma_="bug"),
        FakeToken(idx=9, text="is", pos_="AUX", lemma_="be"),
        FakeToken(idx=12, text="a", pos_="DET", lemma_="a"),
        FakeToken(idx=14, text="nightmare", pos_="NOUN", lemma_="nightmare"),
    ]
    candidates = Highlighter.collect_candidates(full, tokens, score=2)
    starts = {c.start for c in candidates if c.color == "red"}
    assert 5 in starts
    assert 14 in starts


def test_windowed_highlight_colors_incorrect_red(real_nlp, real_lexicon):
    text = Highlighter.windowed_highlight("this seems incorrect for tqdm", score=4)
    assert any(str(s.style) == "red" for s in text.spans)


def test_collect_candidates_tags_incorrect_red_from_override(real_lexicon):
    full = "this seems incorrect for tqdm"
    tokens = [
        FakeToken(idx=0, text="this", pos_="PRON", lemma_="this"),
        FakeToken(idx=5, text="seems", pos_="VERB", lemma_="seem"),
        FakeToken(idx=11, text="incorrect", pos_="ADJ", lemma_="incorrect"),
        FakeToken(idx=21, text="for", pos_="ADP", lemma_="for"),
        FakeToken(idx=25, text="tqdm", pos_="NOUN", lemma_="tqdm"),
    ]
    candidates = Highlighter.collect_candidates(full, tokens, score=4)
    assert any(c.start == 11 and c.color == "red" for c in candidates)
