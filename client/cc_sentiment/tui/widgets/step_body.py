from __future__ import annotations

from textual.containers import Vertical


class StepBody(Vertical):
    DEFAULT_CSS = """
    StepBody {
        width: 100%;
        height: auto;
        margin: 0 0 1 0;
    }
    StepBody > Input {
        margin: 0 0 1 0;
    }
    StepBody > RadioSet {
        width: 100%;
        margin: 0 0 1 0;
        max-height: 12;
        overflow-y: auto;
    }
    StepBody > DataTable {
        width: 100%;
        margin: 0 0 1 0;
        max-height: 12;
    }
    StepBody > .copy-block {
        width: 100%;
        margin: 0 0 1 0;
    }
    StepBody > .status-line {
        width: 100%;
        min-height: 1;
        margin: 0 0 1 0;
    }
    StepBody > StepActions {
        margin: 0;
    }
    StepBody > .after-actions-rule {
        width: 100%;
        height: 1;
        margin: 0 0 1 0;
        border-top: solid $panel-lighten-2;
    }
    StepBody > .after-actions-copy {
        width: 100%;
        max-height: 2;
    }
    """
