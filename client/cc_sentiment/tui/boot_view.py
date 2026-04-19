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


@dataclass
class EngineBootView:
    MAX_SNIPPET_CHARS: ClassVar[int] = 60
    SNIPPET_RATE_LIMIT: ClassVar[float] = 2.5
    SNIPPET_WEIGHTS: ClassVar[dict[int, float]] = {1: 0.7, 2: 0.5, 3: 0.02, 4: 0.5, 5: 0.7}
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
        truncated = snippet if len(snippet) <= self.MAX_SNIPPET_CHARS else snippet[:self.MAX_SNIPPET_CHARS - 1] + "…"
        highlighted = await anyio.to_thread.run_sync(self.highlight_snippet, truncated, score)
        self.lines.append(Text.assemble(
            f"{ScoreBar.ICONS[score]} {score}  \"",
            highlighted,
            "\"  ",
            (comment, "dim"),
        ))
        self.log.update(Text("\n").join(self.lines))

    @classmethod
    def highlight_snippet(cls, snippet: str, score: int) -> Text:
        text = Text(snippet)
        claimed = [False] * len(snippet)
        if score <= 2:
            for m in FRUSTRATION_PATTERN.finditer(snippet):
                start, end = m.start(), m.end()
                text.stylize("red", start, end)
                for i in range(start, end):
                    claimed[i] = True
        nlp = NLP.get()
        if nlp is None:
            return text
        tokens = list(nlp(snippet))
        for i, tok in enumerate(tokens):
            lemma = tok.lemma_.lower()
            start, end = tok.idx, tok.idx + len(tok.text)
            if any(claimed[start:end]):
                continue
            is_profanity = lemma in cls.PROFANITY_TOKENS
            if is_profanity:
                color = "red"
            elif tok.pos_ not in cls.SENTIMENT_POS:
                continue
            elif lemma in cls.POSITIVE_LEMMAS:
                color = "green"
            elif lemma in cls.NEGATIVE_LEMMAS:
                color = "red"
            else:
                continue
            if not is_profanity and cls.is_negated(tokens, i):
                color = "red" if color == "green" else "green"
            text.stylize(color, start, end)
            for j in range(start, end):
                claimed[j] = True
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
