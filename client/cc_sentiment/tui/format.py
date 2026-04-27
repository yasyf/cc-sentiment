from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class VerdictResult:
    text: str
    token: str


class Verdict:
    THRESHOLDS: ClassVar[tuple[tuple[float, str, str], ...]] = (
        (2.0, "Developers are frustrated.", "$error"),
        (2.5, "Developers are struggling.", "$error"),
        (3.5, "Developers are getting by.", "$warning"),
        (4.0, "Developers are happy.", "$success"),
        (float("inf"), "Developers are thriving.", "$success"),
    )

    @classmethod
    def for_avg(cls, avg: float) -> VerdictResult:
        return next(
            VerdictResult(text=text, token=token)
            for cutoff, text, token in cls.THRESHOLDS
            if avg < cutoff
        )


class ScoreEmoji:
    BY_SCORE: ClassVar[dict[int, str]] = {1: "😡", 2: "😒", 3: "😐", 4: "😊", 5: "🤩"}

    @classmethod
    def for_score(cls, score: int) -> str:
        return cls.BY_SCORE[score]

    @classmethod
    def for_avg(cls, avg: float) -> str:
        return cls.for_score(round(avg))


class TimeFormat:
    @staticmethod
    def format_duration(seconds: float) -> str:
        if seconds < 30:
            return "a few seconds"
        if seconds < 3600:
            return f"~{max(1, round(seconds / 60))} min"
        hours = max(1, round(seconds / 3600))
        return f"~{hours} hour" if hours == 1 else f"~{hours} hours"

    @staticmethod
    def format_hms(seconds: float) -> str:
        s = max(0, int(seconds))
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    @staticmethod
    def format_hour(hour: int) -> str:
        match hour:
            case 0:
                return "12am"
            case h if h < 12:
                return f"{h}am"
            case 12:
                return "12pm"
            case h:
                return f"{h - 12}pm"

    @staticmethod
    def format_hour_short(hour: int) -> str:
        match hour:
            case 0:
                return "12a"
            case h if h < 12:
                return f"{h}a"
            case 12:
                return "12p"
            case h:
                return f"{h - 12}p"
