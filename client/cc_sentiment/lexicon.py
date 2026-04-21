from __future__ import annotations

import asyncio
import warnings
from typing import ClassVar

import anyio.to_thread

with warnings.catch_warnings():
    warnings.simplefilter("ignore", SyntaxWarning)
    from afinn import Afinn


class Lexicon:
    DOMAIN_OVERRIDES: ClassVar[dict[str, int]] = {
        "stop": -3, "halt": -3, "quit": -3, "cease": -3,
        "guess": -2, "guessing": -2,
        "continue": 2, "proceed": 2, "resume": 2,
        "break": -2, "nope": -2, "broken": -3, "garbage": -3,
        "nightmare": -3, "absurd": -2, "bug": -2, "hang": -2,
        "freeze": -2, "slow": -2, "trash": -3, "regression": -2,
        "flaky": -2, "impossible": -2, "incorrect": -2,
        "exactly": 2, "finally": 2, "incredible": 3, "smooth": 2,
        "neat": 2, "magic": 2, "work": 2, "correct": 2, "solve": 2,
        "fix": 2, "done": 2, "ship": 2, "crisp": 2, "tight": 2,
    }
    MIN_MAGNITUDE: ClassVar[int] = 2
    afinn: ClassVar[Afinn | None] = None
    locks_by_loop: ClassVar[dict[int, asyncio.Lock]] = {}

    @classmethod
    async def ensure_ready(cls) -> None:
        if cls.afinn is not None:
            return
        loop_id = id(asyncio.get_running_loop())
        lock = cls.locks_by_loop.setdefault(loop_id, asyncio.Lock())
        async with lock:
            if cls.afinn is None:
                cls.afinn = await anyio.to_thread.run_sync(cls.build)

    @staticmethod
    def build() -> Afinn:
        return Afinn(language="en", emoticons=False)

    @classmethod
    def polarity(cls, lemma: str) -> int:
        lower = lemma.lower()
        if (override := cls.DOMAIN_OVERRIDES.get(lower)) is not None:
            return override
        assert cls.afinn is not None, "Lexicon.ensure_ready() must be awaited at startup"
        score = int(cls.afinn.score(lower))
        return score if abs(score) >= cls.MIN_MAGNITUDE else 0
