from __future__ import annotations

from typing import TypeVar

from textual.screen import ModalScreen

T = TypeVar("T")


class Dialog(ModalScreen[T]):
    """Transient prompts — small centered cards layered over the current screen.

    - Full-page flows (BootingScreen, SetupScreen, PlatformErrorScreen,
      CostReviewScreen) extend Screen. They own the whole viewport.
    - Transient prompts (StatShareScreen, DaemonPromptScreen, future confirm
      dialogs) extend Dialog. Max 60 cols x 16 rows, transparent backdrop so
      the underlying progress screen stays visible.
    """

    DEFAULT_CSS = """
    Dialog { align: center middle; background: transparent; }
    Dialog > #dialog-box {
        width: auto;
        max-width: 60;
        min-width: 40;
        height: auto;
        max-height: 16;
        border: heavy $accent;
        padding: 1 2;
        background: $panel;
    }
    Dialog > #dialog-box Button { margin: 1 1 0 0; }
    Dialog > #dialog-box .title { width: 100%; text-style: bold; color: $text; margin: 0 0 1 0; }
    Dialog > #dialog-box .detail { width: 100%; color: $text-muted; margin: 0 0 1 0; }
    Dialog > #dialog-box .emphasis { width: 100%; color: $text; margin: 0 0 2 0; }
    """
