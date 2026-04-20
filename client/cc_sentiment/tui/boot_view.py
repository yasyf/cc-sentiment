from __future__ import annotations

import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from typing import TYPE_CHECKING

import anyio
import anyio.to_thread
from rich.text import Text
from textual.app import App
from textual.widget import Widget
from textual.widgets import Static

from cc_sentiment.engines.filter import FRUSTRATION_PATTERN
from cc_sentiment.nlp import NLP

from cc_sentiment.tui.widgets import ScoreBar

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


@dataclass
class EngineBootView:
    MAX_SNIPPET_CHARS: ClassVar[int] = 60
    SNIPPET_RATE_LIMIT: ClassVar[float] = 2.5
    SNIPPET_WEIGHTS: ClassVar[dict[int, float]] = {1: 0.7, 2: 0.5, 3: 0.02, 4: 0.5, 5: 0.7}
    FALLBACK_MIN_LEN: ClassVar[int] = 3
    NEGATIVE_LEMMAS: ClassVar[frozenset[str]] = frozenset({
        "break", "wrong", "fail", "error", "stuck", "confuse", "nope", "useless",
        "terrible", "awful", "frustrate", "hate", "suck", "annoy", "broken",
        "confused", "frustrating", "annoying",
        "garbage", "mess", "disaster", "nightmare", "horrible", "pathetic",
        "ridiculous", "absurd", "bug", "crash", "hang", "freeze", "slow",
        "dumb", "worst", "trash", "stupid", "idiotic", "insane", "infuriating",
        "painful", "mistake", "regression", "flaky", "impossible", "bad",
    })
    POSITIVE_LEMMAS: ClassVar[frozenset[str]] = frozenset({
        "perfect", "great", "nice", "awesome", "exactly", "beautiful", "love",
        "finally", "amazing", "incredible", "brilliant", "excellent", "wonderful",
        "fantastic", "thank",
        "smooth", "clean", "elegant", "clever", "neat", "sweet", "magic",
        "win", "work", "correct", "solve", "fix", "done", "ship",
        "lovely", "crisp", "tight", "solid",
    })
    PROFANITY_TOKENS: ClassVar[frozenset[str]] = frozenset({
        "fuck", "shit", "damn", "hell", "crap", "bastard", "bitch",
        "piss", "ass", "asshole", "bollocks", "bullshit", "dammit", "goddamn",
    })
    NEGATION_TOKENS: ClassVar[frozenset[str]] = frozenset({
        "not", "no", "never", "nothing", "hardly", "barely",
    })
    SENTIMENT_POS: ClassVar[frozenset[str]] = frozenset({"ADJ", "ADV", "VERB", "INTJ", "NOUN"})
    WITTY_COMMENTS: ClassVar[dict[int, tuple[str, ...]]] = {
        1: ("oof", "yikes", "time to take a walk", "we've all been there", "send help", "cursed"),
        2: ("mood", "same energy", "try again later", "nope nope nope", "sigh", "bargaining stage"),
        3: ("just business", "getting it done", "ok then", "fine", "the work continues", "transactional"),
        4: ("nice", "smooth", "working as intended", "as you were", "on track", "we're cooking"),
        5: ("vibes", "flow state", "chef's kiss", "absolute unit", "sparkles", "heck yeah"),
    }

    app: App
    section: Widget
    log: Static
    lines: deque[Text] = field(default_factory=lambda: deque(maxlen=8))
    last_snippet_at: float = 0.0
    last_snippet_score: int | None = None

    def show(self, engine: str) -> None:
        self.lines.clear()
        self.log.update("")
        self.last_snippet_at = 0.0
        self.last_snippet_score = None
        self.section.remove_class("inactive")

    def hide(self) -> None:
        self.section.add_class("inactive")

    async def add_snippet(self, snippet: str, score: int) -> None:
        now = time.monotonic()
        if now - self.last_snippet_at < self.SNIPPET_RATE_LIMIT:
            return
        if score == self.last_snippet_score:
            return
        if random.random() > self.SNIPPET_WEIGHTS[score]:
            return
        self.last_snippet_at = now
        self.last_snippet_score = score
        comment = random.choice(self.WITTY_COMMENTS[score])
        highlighted = await anyio.to_thread.run_sync(self.windowed_highlight, snippet, score)
        self.lines.append(Text.assemble(
            f"{ScoreBar.ICONS[score]} {score}  \"",
            highlighted,
            "\"  ",
            (comment, "dim"),
        ))
        self.log.update(Text("\n").join(self.lines))

    @classmethod
    def windowed_highlight(cls, full: str, score: int) -> Text:
        width = cls.MAX_SNIPPET_CHARS
        if (nlp := NLP.get()) is None:
            return cls.prefix_highlight(full, width, score)
        tokens = list(nlp(full))
        candidates = cls.collect_candidates(full, tokens, score)
        if (anchor := cls.pick_anchor(candidates)) is None:
            if (anchor := cls.fallback_anchor(tokens, score)) is None:
                return cls.prefix_highlight(full, width, score)
            if anchor.color:
                candidates = [anchor]
        return cls.apply_styles(cls.slice_window(full, anchor, width), candidates)

    @classmethod
    def prefix_highlight(cls, full: str, width: int, score: int) -> Text:
        body = full[:width - 1] + "…" if len(full) > width else full
        text = Text(body)
        if score <= 2:
            for m in FRUSTRATION_PATTERN.finditer(body):
                text.stylize("red", m.start(), m.end())
        return text

    @classmethod
    def collect_candidates(
        cls, full: str, tokens: list[spacy.tokens.Token], score: int,
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
            if lemma in cls.POSITIVE_LEMMAS:
                color = "green"
            elif lemma in cls.NEGATIVE_LEMMAS:
                color = "red"
            else:
                continue
            if cls.is_negated(tokens, i):
                color = "red" if color == "green" else "green"
            spans.append(HighlightSpan(start, end, color, priority=2))
        return spans

    @classmethod
    def pick_anchor(cls, candidates: list[HighlightSpan]) -> HighlightSpan | None:
        return min(candidates, key=lambda s: (-s.priority, s.start), default=None)

    @classmethod
    def fallback_anchor(
        cls, tokens: list[spacy.tokens.Token], score: int,
    ) -> HighlightSpan | None:
        eligible = [
            t for t in tokens
            if t.pos_ in cls.SENTIMENT_POS
            and len(t.text) >= cls.FALLBACK_MIN_LEN
            and t.text.isalpha()
        ]
        if not eligible:
            return None
        tok = max(eligible, key=lambda t: len(t.text))
        match score:
            case 3:
                color = ""
            case s if s <= 2:
                color = "red"
            case _:
                color = "green"
        return HighlightSpan(tok.idx, tok.idx + len(tok.text), color, priority=1)

    @classmethod
    def slice_window(
        cls, full: str, anchor: HighlightSpan, width: int,
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
                body="…" + full[ks:ks + kept_c] + "…",
                full_offset=ks,
                kept_len=kept_c,
                leading=True,
            )
        if anchor.end <= width - 1:
            return WindowedSlice(
                body=full[:width - 1] + "…",
                full_offset=0,
                kept_len=width - 1,
                leading=False,
            )
        if anchor.start >= n - width + 1:
            return WindowedSlice(
                body="…" + full[n - width + 1:],
                full_offset=n - width + 1,
                kept_len=width - 1,
                leading=True,
            )
        return WindowedSlice(
            body=full[:width - 1] + "…",
            full_offset=0,
            kept_len=width - 1,
            leading=False,
        )

    @classmethod
    def apply_styles(
        cls, window: WindowedSlice, candidates: list[HighlightSpan],
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
