from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Static

from cc_sentiment.engines import ClaudeCLIEngine
from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.widgets import ButtonRow


class CostReviewScreen(Dialog[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.cost = ClaudeCLIEngine.estimate_cost_usd(bucket_count)

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label("Use Claude to score?", classes="title")
            yield Static(
                f"This Mac can't run scoring locally, so we'll use Claude through your account "
                f"to score [b]{self.bucket_count}[/] new conversations.",
                classes="detail",
            )
            yield Static(
                f"About [b]${self.cost:.2f}[/]. Real cost is usually lower thanks to caching.",
                classes="emphasis",
            )
            yield Static(
                "Billed by Anthropic to your existing Claude account. "
                "Your conversation text leaves this Mac only for that one API call.",
                classes="detail",
            )
            yield ButtonRow(
                Button("Continue", id="cost-yes", variant="primary"),
                Button("Cancel", id="cost-no", variant="default"),
            )

    @on(Button.Pressed, "#cost-yes")
    def on_confirm(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#cost-no")
    def on_cancel(self) -> None:
        self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)
