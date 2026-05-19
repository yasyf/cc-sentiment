from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

# Event-loop-only: deque.append and list(deque) are GIL-atomic, no lock needed.


@dataclass
class DebugLog:
    lines: deque[str] = field(default_factory=lambda: deque(maxlen=400))

    _instance: ClassVar[DebugLog | None] = None

    @classmethod
    def get(cls) -> DebugLog:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        cls._instance = None

    def append(self, source: str, line: str) -> None:
        if not (stripped := line.strip()):
            return
        self.lines.append(f"{time.strftime('%H:%M:%S')} [{source}] {stripped}")

    def snapshot(self) -> str:
        return "\n".join(self.lines)
