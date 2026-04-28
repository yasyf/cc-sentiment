from __future__ import annotations

import pytest
from afinn import Afinn

from cc_sentiment.lexicon import Lexicon


@pytest.fixture
def real_lexicon(monkeypatch):
    monkeypatch.setattr(
        "cc_sentiment.lexicon.Lexicon.afinn",
        Afinn(language="en", emoticons=False),
    )


def test_polarity_afinn_negative_over_threshold(real_lexicon):
    assert Lexicon.polarity("useless") < 0
    assert Lexicon.polarity("hate") < 0
    assert Lexicon.polarity("horrible") < 0


def test_polarity_afinn_positive_over_threshold(real_lexicon):
    assert Lexicon.polarity("perfect") > 0
    assert Lexicon.polarity("amazing") > 0
    assert Lexicon.polarity("lovely") > 0


def test_polarity_afinn_weak_score_filtered(real_lexicon):
    assert Lexicon.polarity("want") == 0


def test_polarity_non_lexicon_word_is_zero(real_lexicon):
    assert Lexicon.polarity("hello") == 0
    assert Lexicon.polarity("deployment") == 0


def test_polarity_domain_override_stop_is_negative(real_lexicon):
    assert Lexicon.polarity("stop") < 0
    assert Lexicon.polarity("halt") < 0
    assert Lexicon.polarity("guessing") < 0


def test_polarity_domain_override_continue_is_positive(real_lexicon):
    assert Lexicon.polarity("continue") > 0
    assert Lexicon.polarity("proceed") > 0


def test_polarity_domain_override_restores_claude_code_domain_words(real_lexicon):
    assert Lexicon.polarity("bug") < 0
    assert Lexicon.polarity("broken") < 0
    assert Lexicon.polarity("flaky") < 0
    assert Lexicon.polarity("work") > 0
    assert Lexicon.polarity("fix") > 0
    assert Lexicon.polarity("done") > 0


def test_polarity_lowercases_input(real_lexicon):
    assert Lexicon.polarity("STOP") < 0
    assert Lexicon.polarity("Continue") > 0
    assert Lexicon.polarity("PERFECT") > 0


def test_polarity_domain_override_bypasses_threshold(real_lexicon):
    assert Lexicon.polarity("solve") > 0
