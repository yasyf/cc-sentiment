from __future__ import annotations

from textual.containers import Vertical


class Section(Vertical):
    DEFAULT_CSS = """
    Section { height: auto; }
    Section.inactive { display: none; }
    """
