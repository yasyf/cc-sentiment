from __future__ import annotations


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
