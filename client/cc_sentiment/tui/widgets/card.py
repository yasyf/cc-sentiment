from __future__ import annotations

from typing import ClassVar

from cc_sentiment.tui.widgets.section import Section


class Card(Section):
    DEFAULT_CSS: ClassVar[str] = """
    Card { border: round $surface; padding: 0 1; height: auto; }
    """

    def __init__(self, *children, title: str, **kwargs) -> None:
        super().__init__(*children, **kwargs)
        self.border_title = title
