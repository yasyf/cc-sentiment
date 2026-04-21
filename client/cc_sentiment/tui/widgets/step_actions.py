from __future__ import annotations

from textual.containers import Horizontal
from textual.widgets import Button, Static


class StepActions(Horizontal):
    DEFAULT_CSS = """
    StepActions {
        width: 100%;
        height: auto;
        margin: 1 0 0 0;
        align-horizontal: right;
    }
    StepActions > .step-actions-spacer {
        width: 1fr;
    }
    StepActions > Button {
        margin: 0 0 0 1;
    }
    """

    def __init__(self, *buttons: Button, primary: Button, **kwargs) -> None:
        if sum(button.variant == "primary" for button in (*buttons, primary)) != 1:
            raise ValueError("StepActions requires exactly one primary button")
        if primary.variant != "primary":
            raise ValueError("StepActions requires exactly one primary button")
        super().__init__(
            Static("", classes="step-actions-spacer"),
            *buttons,
            primary,
            **kwargs,
        )
