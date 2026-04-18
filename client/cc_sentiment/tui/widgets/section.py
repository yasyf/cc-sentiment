from __future__ import annotations

from textual.containers import Vertical


class Section(Vertical):
    DEFAULT_CSS = """
    Section {
        height: auto;
        margin: 1 0 0 0;
        padding: 0 1;
        border: round $primary-background;
    }
    Section.inactive { display: none; }
    Section.dim { border: round $surface; }
    """

    def __init__(self, *children, title: str | None = None, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        if title is not None:
            self.border_subtitle = title
