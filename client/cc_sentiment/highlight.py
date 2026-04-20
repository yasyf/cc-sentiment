from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar

from rich.text import Text

from cc_sentiment.engines.filter import FRUSTRATION_PATTERN
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.nlp import NLP

if TYPE_CHECKING:
    import spacy.tokens


@dataclass(frozen=True)
class HighlightSpan:
    start: int
    end: int
    color: str
    priority: int


@dataclass(frozen=True)
class WindowedSlice:
    body: str
    full_offset: int
    kept_len: int
    leading: bool


class Highlighter:
    MAX_SNIPPET_CHARS: ClassVar[int] = 60
    FALLBACK_MIN_LEN: ClassVar[int] = 3
    PROFANITY_TOKENS: ClassVar[frozenset[str]] = frozenset(
        {
            "fuck", "fucks", "fucker", "fuckers", "fucking", "fucked", "fuckin",
            "motherfucker", "motherfuckers", "motherfucking", "mf", "mofo",
            "shit", "shits", "shitty", "shite", "shitshow", "shitstorm", "shithead",
            "bullshit", "horseshit",
            "damn", "damned", "damnit", "dammit",
            "goddamn", "goddamned", "goddammit", "goddamnit",
            "hell", "hellish",
            "crap", "crappy",
            "piss", "pissed", "pissing",
            "ass", "asses", "asshole", "assholes", "asshat",
            "arse", "arsehole", "arseholes",
            "dumbass", "jackass", "smartass",
            "bastard", "bastards",
            "bitch", "bitches", "bitchy", "bitching",
            "bollocks", "bollox",
            "wanker", "wankers", "twat", "twats",
            "prick", "pricks", "dickhead", "dickheads",
            "bugger", "buggered", "bloody",
            "feck", "fecking",
            "frick", "frickin", "freakin", "freaking", "frigging",
            "wtf", "ffs", "jfc", "stfu", "gtfo",
        }
    )
    NEGATION_TOKENS: ClassVar[frozenset[str]] = frozenset(
        {"not", "no", "never", "nothing", "hardly", "barely"}
    )
    SENTIMENT_POS: ClassVar[frozenset[str]] = frozenset(
        {"ADJ", "ADV", "VERB", "INTJ", "NOUN"}
    )

    @classmethod
    def windowed_highlight(cls, full: str, score: int) -> Text:
        width = cls.MAX_SNIPPET_CHARS
        if (nlp := NLP.get()) is None:
            return cls.prefix_highlight(full, width, score)
        tokens = list(nlp(full))
        candidates = cls.collect_candidates(full, tokens, score)
        if (anchor := cls.pick_anchor(candidates)) is None:
            if (anchor := cls.fallback_anchor(tokens)) is None:
                return cls.prefix_highlight(full, width, score)
        return cls.apply_styles(cls.slice_window(full, anchor, width), candidates)

    @classmethod
    def prefix_highlight(cls, full: str, width: int, score: int) -> Text:
        body = full[: width - 1] + "…" if len(full) > width else full
        text = Text(body)
        if score <= 2:
            for m in FRUSTRATION_PATTERN.finditer(body):
                text.stylize("red", m.start(), m.end())
        return text

    @classmethod
    def collect_candidates(
        cls,
        full: str,
        tokens: list[spacy.tokens.Token],
        score: int,
    ) -> list[HighlightSpan]:
        spans: list[HighlightSpan] = []
        if score <= 2:
            spans.extend(
                HighlightSpan(m.start(), m.end(), "red", priority=3)
                for m in FRUSTRATION_PATTERN.finditer(full)
            )
        for i, tok in enumerate(tokens):
            lemma = tok.lemma_.lower()
            start, end = tok.idx, tok.idx + len(tok.text)
            if lemma in cls.PROFANITY_TOKENS:
                spans.append(HighlightSpan(start, end, "red", priority=3))
                continue
            if tok.pos_ not in cls.SENTIMENT_POS:
                continue
            polarity = Lexicon.polarity(lemma)
            if polarity == 0:
                continue
            color = "green" if polarity > 0 else "red"
            if cls.is_negated(tokens, i):
                color = "red" if color == "green" else "green"
            spans.append(HighlightSpan(start, end, color, priority=2))
        return spans

    @classmethod
    def pick_anchor(cls, candidates: list[HighlightSpan]) -> HighlightSpan | None:
        return min(candidates, key=lambda s: (-s.priority, s.start), default=None)

    @classmethod
    def fallback_anchor(
        cls,
        tokens: list[spacy.tokens.Token],
    ) -> HighlightSpan | None:
        eligible = [
            t
            for t in tokens
            if t.pos_ in cls.SENTIMENT_POS
            and len(t.text) >= cls.FALLBACK_MIN_LEN
            and t.text.isalpha()
        ]
        if not eligible:
            return None
        tok = max(eligible, key=lambda t: len(t.text))
        return HighlightSpan(tok.idx, tok.idx + len(tok.text), "", priority=1)

    @classmethod
    def slice_window(
        cls,
        full: str,
        anchor: HighlightSpan,
        width: int,
    ) -> WindowedSlice:
        n = len(full)
        if n <= width:
            return WindowedSlice(body=full, full_offset=0, kept_len=n, leading=False)
        kept_c = width - 2
        lo, hi = max(1, anchor.end - kept_c), min(n - 1 - kept_c, anchor.start)
        if anchor.end - anchor.start <= kept_c and lo <= hi:
            ideal = (anchor.start + anchor.end) // 2 - kept_c // 2
            ks = max(lo, min(ideal, hi))
            return WindowedSlice(
                body="…" + full[ks : ks + kept_c] + "…",
                full_offset=ks,
                kept_len=kept_c,
                leading=True,
            )
        if anchor.end <= width - 1:
            return WindowedSlice(
                body=full[: width - 1] + "…",
                full_offset=0,
                kept_len=width - 1,
                leading=False,
            )
        if anchor.start >= n - width + 1:
            return WindowedSlice(
                body="…" + full[n - width + 1 :],
                full_offset=n - width + 1,
                kept_len=width - 1,
                leading=True,
            )
        return WindowedSlice(
            body=full[: width - 1] + "…",
            full_offset=0,
            kept_len=width - 1,
            leading=False,
        )

    @classmethod
    def apply_styles(
        cls,
        window: WindowedSlice,
        candidates: list[HighlightSpan],
    ) -> Text:
        text = Text(window.body)
        claimed = [False] * len(window.body)
        shift = (1 if window.leading else 0) - window.full_offset
        keep_end = window.full_offset + window.kept_len
        for span in sorted(candidates, key=lambda s: -s.priority):
            if not span.color:
                continue
            if span.start < window.full_offset or span.end > keep_end:
                continue
            ts, te = span.start + shift, span.end + shift
            if any(claimed[ts:te]):
                continue
            text.stylize(span.color, ts, te)
            for i in range(ts, te):
                claimed[i] = True
        return text

    @classmethod
    def is_negated(cls, tokens: list[spacy.tokens.Token], idx: int) -> bool:
        preceding: list[spacy.tokens.Token] = []
        j = idx - 1
        while j >= 0 and len(preceding) < 2:
            if tokens[j].pos_ not in ("PUNCT", "SPACE"):
                preceding.append(tokens[j])
            j -= 1
        return any(t.lemma_.lower() in cls.NEGATION_TOKENS for t in preceding)
