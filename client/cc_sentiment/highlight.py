from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import groupby
from typing import ClassVar, NamedTuple

from rich.text import Text

from cc_sentiment.engines.filter import FRUSTRATION_PATTERN
from cc_sentiment.lexicon import Lexicon

WORD_BOUNDARY_PATTERN = re.compile(r"\b[a-zA-Z]+\b")


class Token(NamedTuple):
    lower: str
    start: int
    end: int


def tokenize_spans(text: str) -> list[Token]:
    spans: list[Token] = []
    pos = 0
    for alpha, group in groupby(text, str.isalpha):
        run = "".join(group)
        if alpha:
            spans.append(Token(run.lower(), pos, pos + len(run)))
        pos += len(run)
    return spans


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
    MAX_SNAP_DISTANCE: ClassVar[int] = 15
    FALLBACK_MIN_LEN: ClassVar[int] = 3
    NEGATIVE_TONE_MAX: ClassVar[int] = 2
    POSITIVE_TONE_MIN: ClassVar[int] = 4
    PYTHON_LITERALS: ClassVar[frozenset[str]] = frozenset({"True", "False", "None"})
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
        {"not", "no", "never", "nothing", "hardly", "barely", "cannot"}
    )

    @classmethod
    def profanity_tokens_in(cls, text: str) -> list[str]:
        return [
            word for word in WORD_BOUNDARY_PATTERN.findall(text.lower())
            if word in cls.PROFANITY_TOKENS
        ]

    @staticmethod
    def after_equals(text: str, start: int) -> bool:
        j = start - 1
        while j >= 0 and text[j] in " \t":
            j -= 1
        return j >= 0 and text[j] == "="

    @classmethod
    def message_polarity(cls, text: str) -> int:
        tokens = tokenize_spans(text)
        polarity = 0
        for i, tok in enumerate(tokens):
            if tok.lower in cls.PROFANITY_TOKENS:
                polarity -= 3
                continue
            if text[tok.start : tok.end] in cls.PYTHON_LITERALS:
                continue
            if cls.after_equals(text, tok.start):
                continue
            score = Lexicon.polarity(tok.lower)
            if score == 0:
                continue
            if cls.is_negated(tokens, i):
                score = -score
            polarity += score
        return polarity - 3 * len(FRUSTRATION_PATTERN.findall(text))

    @classmethod
    def windowed_highlight(cls, full: str, score: int) -> Text:
        width = cls.MAX_SNIPPET_CHARS
        candidates = cls.collect_candidates(full, score)
        if (anchor := cls.pick_anchor(candidates)) is None:
            if (anchor := cls.fallback_anchor(full)) is None:
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
    def collect_candidates(cls, full: str, score: int) -> list[HighlightSpan]:
        spans: list[HighlightSpan] = []
        if score <= cls.NEGATIVE_TONE_MAX:
            spans.extend(
                HighlightSpan(m.start(), m.end(), "red", priority=3)
                for m in FRUSTRATION_PATTERN.finditer(full)
            )
        tokens = tokenize_spans(full)
        for i, tok in enumerate(tokens):
            if tok.lower in cls.PROFANITY_TOKENS:
                spans.append(HighlightSpan(tok.start, tok.end, "red", priority=3))
                continue
            if full[tok.start : tok.end] in cls.PYTHON_LITERALS:
                continue
            if cls.after_equals(full, tok.start):
                continue
            polarity = Lexicon.polarity(tok.lower)
            if polarity == 0:
                continue
            color = "green" if polarity > 0 else "red"
            if cls.is_negated(tokens, i):
                color = "red" if color == "green" else "green"
            if score <= cls.NEGATIVE_TONE_MAX and color == "green":
                continue
            if score >= cls.POSITIVE_TONE_MIN and color == "red":
                continue
            spans.append(HighlightSpan(tok.start, tok.end, color, priority=2))
        return spans

    @classmethod
    def pick_anchor(cls, candidates: list[HighlightSpan]) -> HighlightSpan | None:
        return min(candidates, key=lambda s: (-s.priority, s.start), default=None)

    @classmethod
    def fallback_anchor(cls, full: str) -> HighlightSpan | None:
        eligible = [
            tok for tok in tokenize_spans(full)
            if tok.end - tok.start >= cls.FALLBACK_MIN_LEN
        ]
        if not eligible:
            return None
        tok = max(eligible, key=lambda t: t.end - t.start)
        return HighlightSpan(tok.start, tok.end, "", priority=1)

    @classmethod
    def snap_start_forward(cls, full: str, start: int, anchor_start: int) -> int:
        if start <= 0 or start >= len(full):
            return start
        if full[start].isspace() or full[start - 1].isspace():
            return start
        limit = min(start + cls.MAX_SNAP_DISTANCE, anchor_start)
        ws = full.find(" ", start, limit)
        return ws + 1 if ws != -1 else start

    @classmethod
    def snap_end_backward(cls, full: str, end: int, anchor_end: int) -> int:
        if end <= 0 or end >= len(full):
            return end
        if full[end].isspace() or full[end - 1].isspace():
            return end
        limit = max(end - cls.MAX_SNAP_DISTANCE, anchor_end)
        ws = full.rfind(" ", limit, end)
        return ws if ws != -1 else end

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
            start = cls.snap_start_forward(full, ks, anchor.start)
            end = cls.snap_end_backward(full, ks + kept_c, anchor.end)
            return WindowedSlice(
                body="…" + full[start:end] + "…",
                full_offset=start,
                kept_len=end - start,
                leading=True,
            )
        if anchor.end <= width - 1:
            end = cls.snap_end_backward(full, width - 1, anchor.end)
            return WindowedSlice(
                body=full[:end] + "…",
                full_offset=0,
                kept_len=end,
                leading=False,
            )
        if anchor.start >= n - width + 1:
            start = cls.snap_start_forward(full, n - width + 1, anchor.start)
            return WindowedSlice(
                body="…" + full[start:],
                full_offset=start,
                kept_len=n - start,
                leading=True,
            )
        end = cls.snap_end_backward(full, width - 1, anchor.end)
        return WindowedSlice(
            body=full[:end] + "…",
            full_offset=0,
            kept_len=end,
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
    def is_negated(cls, tokens: list[Token], idx: int) -> bool:
        return any(
            tokens[j].lower in cls.NEGATION_TOKENS
            for j in range(max(0, idx - 2), idx)
        )
