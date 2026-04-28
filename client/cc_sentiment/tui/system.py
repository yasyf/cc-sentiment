from __future__ import annotations

import shutil
import subprocess
import sys
import webbrowser


class Clipboard:
    @classmethod
    def command(cls) -> list[str] | None:
        match sys.platform:
            case "darwin":
                if shutil.which("pbcopy"):
                    return ["pbcopy"]
            case "win32":
                if shutil.which("clip"):
                    return ["clip"]
            case _:
                if shutil.which("wl-copy"):
                    return ["wl-copy"]
                if shutil.which("xclip"):
                    return ["xclip", "-selection", "clipboard"]
                if shutil.which("xsel"):
                    return ["xsel", "--clipboard", "--input"]
        return None

    @classmethod
    def copy(cls, text: str) -> bool:
        if (cmd := cls.command()) is None:
            return False
        try:
            subprocess.run(cmd, input=text, text=True, check=True, timeout=5)
        except (subprocess.SubprocessError, OSError):
            return False
        return True

    @classmethod
    def available(cls) -> bool:
        return cls.command() is not None


class Browser:
    @staticmethod
    def available() -> bool:
        try:
            return webbrowser.get() is not None
        except webbrowser.Error:
            return False

    @staticmethod
    def open(url: str) -> bool:
        try:
            return bool(webbrowser.open(url))
        except (webbrowser.Error, OSError):
            return False
