from __future__ import annotations

import re
from pathlib import Path


class RuntimeLog:
    CARET_PATTERN = re.compile(rb"\^(?P<char>[\?@A-Z\[\\\]\^_])")
    MOUSE_EVENT_PATTERN = re.compile(rb"\x1b\[<(?P<button>\d+);(?P<x>\d+);(?P<y>\d+)(?P<state>[Mm])")

    @classmethod
    def decode(cls, source: bytes | Path) -> bytes:
        data = source.read_bytes() if isinstance(source, Path) else source
        return cls.CARET_PATTERN.sub(lambda match: cls.control_byte(match.group("char")), data)

    @staticmethod
    def control_byte(char: bytes) -> bytes:
        return b"\x7f" if char == b"?" else bytes((char[0] & 0x1F,))

    @classmethod
    def mouse_events(cls, source: bytes | Path) -> list[dict[str, int | str]]:
        events = [
            {
                "type": "mouse-down" if match.group("state") == b"M" else "mouse-up",
                "button": int(match.group("button")),
                "x": int(match.group("x")),
                "y": int(match.group("y")),
            }
            for match in cls.MOUSE_EVENT_PATTERN.finditer(cls.decode(source))
            if int(match.group("button")) == 0
        ]
        return events + [
            {"type": "click", "button": first["button"], "x": first["x"], "y": first["y"]}
            for first, second in zip(events, events[1:])
            if first["type"] == "mouse-down"
            and second["type"] == "mouse-up"
            and first["button"] == second["button"]
            and first["x"] == second["x"]
            and first["y"] == second["y"]
        ]
