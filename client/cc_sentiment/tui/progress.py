from __future__ import annotations

import time
from dataclasses import dataclass


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
