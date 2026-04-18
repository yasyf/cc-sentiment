from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label

from cc_sentiment.engines import ClaudeCLIEngine
from cc_sentiment.tui.screens.dialog import Dialog


class CostReviewScreen(Dialog[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.cost = ClaudeCLIEngine.estimate_cost_usd(bucket_count)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label(f"Use {self.model} for scoring?", classes="title")
            yield Label(
                f"This machine can't run local inference, so we'll use the Claude API "
                f"via `claude -p` to score [b]{self.bucket_count}[/] new buckets.",
                classes="detail",
            )
            yield Label(
                f"Estimated cost: about [b]${self.cost:.2f}[/] "
                f"(at ${ClaudeCLIEngine.HAIKU_INPUT_USD_PER_MTOK:.2f}/MTok in, "
                f"${ClaudeCLIEngine.HAIKU_OUTPUT_USD_PER_MTOK:.2f}/MTok out). "
                f"Actual cost is often lower thanks to prompt caching.",
                classes="emphasis",
            )
            yield Label(
                "This gets billed by Anthropic through your existing `claude` account. "
                "Your conversation text still leaves the machine only as part of this API call.",
                classes="detail",
            )
            with Horizontal():
                yield Button("Continue", id="cost-yes", variant="primary")
                yield Button("Cancel", id="cost-no", variant="default")

    @on(Button.Pressed, "#cost-yes")
    def on_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cost-no")
    def on_cancel(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
