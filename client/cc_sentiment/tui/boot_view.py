from __future__ import annotations

import random
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

import anyio
import anyio.to_thread
from rich.text import Text
from textual.app import App
from textual.widget import Widget
from textual.widgets import Static

from cc_sentiment.nlp import NLP

from cc_sentiment.tui.widgets import ScoreBar, SpinnerLine


@dataclass
class EngineBootView:
    MAX_SNIPPET_CHARS: ClassVar[int] = 60
    SNIPPET_RATE_LIMIT: ClassVar[float] = 2.5
    SNIPPET_WEIGHTS: ClassVar[dict[int, float]] = {1: 0.7, 2: 0.5, 3: 0.02, 4: 0.5, 5: 0.7}
    NEGATIVE_WORDS: ClassVar[frozenset[str]] = frozenset({
        "broken", "wrong", "fails", "fail", "failed", "failing", "error", "errors",
        "stuck", "confused", "nope", "useless", "terrible", "awful", "frustrating",
        "hate", "hated", "hates", "sucks", "annoying",
    })
    POSITIVE_WORDS: ClassVar[frozenset[str]] = frozenset({
        "perfect", "great", "nice", "awesome", "exactly", "beautiful", "love",
        "loved", "loves", "finally", "amazing", "incredible", "brilliant",
        "excellent", "wonderful", "fantastic",
    })
    SENTIMENT_POS: ClassVar[frozenset[str]] = frozenset({"ADJ", "ADV", "VERB", "INTJ"})
    CODE_PATTERN: ClassVar[re.Pattern[str]] = re.compile(
        r"(\b[\w.-]+\.(py|ts|tsx|js|jsx|md|json|yml|yaml|toml|rs|go|sh|sql)\b"
        r"|\b[a-z][a-z0-9]*_[a-z0-9_]+\b"
        r"|\b[a-z][a-z0-9]*[A-Z][A-Za-z0-9]*\b"
        r"|\b\w+\(\)"
        r"|(?:/|\./)[\w./-]+)"
    )
    WITTY_COMMENTS: ClassVar[dict[int, tuple[str, ...]]] = {
        1: ("oof", "yikes", "time to take a walk", "we've all been there", "send help", "cursed"),
        2: ("mood", "same energy", "try again later", "nope nope nope", "sigh", "bargaining stage"),
        3: ("just business", "getting it done", "ok then", "fine", "the work continues", "transactional"),
        4: ("nice", "smooth", "working as intended", "as you were", "on track", "we're cooking"),
        5: ("vibes", "flow state", "chef's kiss", "absolute unit", "sparkles", "heck yeah"),
    }

    app: App
    section: Widget
    status: SpinnerLine
    log: Static
    lines: deque[Text] = field(default_factory=lambda: deque(maxlen=8))
    last_snippet_at: float = 0.0
    last_snippet_score: int | None = None
    snippet_started: bool = False

    def show(self, engine: str) -> None:
        self.status.spinner.text = f"Loading {engine} engine"
        self.status.display = True
        self.lines.clear()
        self.log.update("")
        self.last_snippet_at = 0.0
        self.last_snippet_score = None
        self.snippet_started = False
        self.section.remove_class("inactive")

    def hide(self) -> None:
        self.section.add_class("inactive")

    def write_from_thread(self, line: str) -> None:
        self.lines.append(Text(line, style="dim"))
        self.app.call_from_thread(self.log.update, Text("\n").join(self.lines))

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
        if not self.snippet_started:
            self.snippet_started = True
            self.lines.clear()
            self.status.display = False
        comment = random.choice(self.WITTY_COMMENTS[score])
        truncated = snippet if len(snippet) <= self.MAX_SNIPPET_CHARS else snippet[:self.MAX_SNIPPET_CHARS - 1] + "…"
        highlighted = await anyio.to_thread.run_sync(self.highlight_snippet, truncated)
        self.lines.append(Text.assemble(
            f"{ScoreBar.ICONS[score]} {score}  \"",
            highlighted,
            "\"  ",
            (comment, "dim"),
        ))
        self.log.update(Text("\n").join(self.lines))

    @classmethod
    def highlight_snippet(cls, snippet: str) -> Text:
        text = Text(snippet)
        claimed = [False] * len(snippet)
        nlp = NLP.get()
        if nlp is not None:
            for tok in nlp(snippet):
                if tok.pos_ not in cls.SENTIMENT_POS:
                    continue
                lower = tok.text.lower()
                color = "red" if lower in cls.NEGATIVE_WORDS else "green" if lower in cls.POSITIVE_WORDS else None
                if color is None:
                    continue
                start, end = tok.idx, tok.idx + len(tok.text)
                if any(claimed[start:end]):
                    continue
                text.stylize(color, start, end)
                for i in range(start, end):
                    claimed[i] = True
        for m in cls.CODE_PATTERN.finditer(snippet):
            start, end = m.start(), m.end()
            if any(claimed[start:end]):
                continue
            text.stylize("cyan", start, end)
            for i in range(start, end):
                claimed[i] = True
        return text
