from __future__ import annotations

from textual.containers import Vertical
from textual.widget import Widget


class StepBody(Vertical):
    DEFAULT_CSS = """
    StepBody {
        width: 100%;
        height: auto;
        padding: 1 0;
    }
    StepBody > Input {
        margin: 0 0 1 0;
    }
    StepBody > RadioSet {
        margin: 0 0 1 0;
        max-height: 12;
        overflow-y: auto;
    }
    StepBody > DataTable {
        margin: 0 0 1 0;
        max-height: 12;
    }
    StepBody > .status-line {
        width: 100%;
        min-height: 1;
        margin: 0 0 1 0;
    }
    StepBody > StepActions {
        margin: 0;
    }
    """

    def __init__(self, *children: Widget, **kwargs) -> None:
        super().__init__(*children, **kwargs)
