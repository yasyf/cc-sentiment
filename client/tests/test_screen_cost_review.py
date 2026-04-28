from __future__ import annotations

from textual.app import App

from cc_sentiment.tui.screens import CostReviewScreen


class CostHarness(App[None]):
    def __init__(self, bucket_count: int, model: str) -> None:
        super().__init__()
        self.bucket_count = bucket_count
        self.model = model
        self.dismissed: bool | None = None

    def on_mount(self) -> None:
        self.push_screen(CostReviewScreen(self.bucket_count, self.model), self._capture)

    def _capture(self, result: bool | None) -> None:
        self.dismissed = result


async def test_cost_review_renders_bucket_count_and_cost():
    harness = CostHarness(500, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        text = " ".join(
            str(w.render()) for w in pilot.app.screen.query("Label, Static")
        )
        assert "500" in text
        assert "Claude" in text


async def test_cost_review_continue_dismisses_true():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-yes")
        await pilot.pause()
        assert harness.dismissed is True


async def test_cost_review_cancel_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.click("#cost-no")
        await pilot.pause()
        assert harness.dismissed is False


async def test_cost_review_escape_dismisses_false():
    harness = CostHarness(100, "claude-haiku-4-5")
    async with harness.run_test() as pilot:
        await pilot.pause()
        await pilot.press("escape")
        await pilot.pause()
        assert harness.dismissed is False
