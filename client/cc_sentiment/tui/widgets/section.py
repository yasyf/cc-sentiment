from __future__ import annotations

from textual.containers import Vertical


class Section(Vertical):
    DEFAULT_CSS = """
    Section { height: auto; margin: 1 0 0 0; }
    Section.inactive { display: none; }
    """
