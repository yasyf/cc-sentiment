from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass, field, fields


@dataclass
class DebugState:
    engine_name: str = "—"
    nlp_state: str = "—"
    prewarm_uvx: str = "—"
    prewarm_model: str = "—"
    card_attempts: int = 0
    card_last_status: str = "idle"
    card_elapsed: float = 0.0
    card_stopped: str | None = None

    def reset(self) -> None:
        for f in fields(self):
            setattr(self, f.name, f.default)


@dataclass
class ScoringProgress:
    start_time: float = 0.0
    initial_estimate_seconds: float | None = None

    def elapsed(self) -> float:
        return time.monotonic() - self.start_time if self.start_time else 0.0

    def begin(self, rate: float | None, total: int) -> None:
        self.start_time = time.monotonic()
        self.initial_estimate_seconds = total / rate if rate and rate > 0 and total > 0 else None

    def projected_total(self, scored: int, total: int) -> float:
        elapsed = self.elapsed()
        if scored > 0 and total > 0:
            return elapsed * total / scored
        if self.initial_estimate_seconds is not None:
            return self.initial_estimate_seconds
        return elapsed

    def rate(self, scored: int) -> float:
        elapsed = self.elapsed()
        return scored / elapsed if elapsed > 0 else 0.0

    def reset(self) -> None:
        self.start_time = 0.0
        self.initial_estimate_seconds = None


@dataclass
class LiveFunStats:
    swear_counts: Counter[str] = field(default_factory=Counter)

    def bump(self, words: list[str]) -> None:
        self.swear_counts.update(words)

    def top(self) -> tuple[str, int] | None:
        return self.swear_counts.most_common(1)[0] if self.swear_counts else None

    def reset(self) -> None:
        self.swear_counts.clear()
