from __future__ import annotations

import random
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

from cc_sentiment.highlight import Highlighter
from cc_sentiment.tui.dashboard.format import ScoreEmoji


@dataclass
class MomentsView:
    SNIPPET_RATE_LIMIT: ClassVar[float] = 2.5
    SNIPPET_WEIGHTS: ClassVar[dict[int, float]] = {
        1: 0.7,
        2: 0.5,
        3: 0.02,
        4: 0.5,
        5: 0.7,
    }
    WITTY_COMMENTS: ClassVar[dict[int, tuple[str, ...]]] = {
        1: (
            "oof",
            "yikes",
            "time to take a walk",
            "we've all been there",
            "send help",
            "cursed",
        ),
        2: (
            "mood",
            "same energy",
            "try again later",
            "nope nope nope",
            "sigh",
            "bargaining stage",
        ),
        3: (
            "just business",
            "getting it done",
            "ok then",
            "fine",
            "the work continues",
            "transactional",
        ),
        4: (
            "nice",
            "smooth",
            "working as intended",
            "as you were",
            "on track",
            "we're cooking",
        ),
        5: (
            "vibes",
            "flow state",
            "chef's kiss",
            "absolute unit",
            "sparkles",
            "heck yeah",
        ),
    }

    app: App
    section: Widget
    log: Static
    lines: deque[Text] = field(default_factory=lambda: deque(maxlen=8))
    last_snippet_at: float = 0.0
    last_snippet_score: int | None = None

    def show(self) -> None:
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
        highlighted = await anyio.to_thread.run_sync(
            Highlighter.windowed_highlight, snippet, score
        )
        self.lines.append(
            Text.assemble(
                f'{ScoreEmoji.for_score(score)} {score}  "',
                highlighted,
                '"  ',
                (comment, "dim"),
            )
        )
        self.log.update(Text("\n").join(self.lines))
