from __future__ import annotations

from textual.widgets import Button, DataTable

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import InitialScreen

from .conftest import has_text, mounted


def gs_initial() -> GlobalState:
    return GlobalState(stage=Stage.INITIAL)


class TestInitialScreen:
    """Strict codification of initial.py — spare loading screen, spinner only."""

    async def test_status_line_renders_checking_text(self):
        async with mounted(InitialScreen, gs_initial()) as pilot:
            status = pilot.app.screen.query_one("#status")
            assert "Checking your setup" in str(status.render())

    async def test_no_buttons(self):
        async with mounted(InitialScreen, gs_initial()) as pilot:
            assert not pilot.app.screen.query(Button)

    async def test_no_table(self):
        async with mounted(InitialScreen, gs_initial()) as pilot:
            assert not pilot.app.screen.query(DataTable)

    async def test_no_borderless_card_title(self):
        # Docstring: "Centered, no border" — no titled card.
        async with mounted(InitialScreen, gs_initial()) as pilot:
            assert not pilot.app.screen.query("#title")

    async def test_no_elapsed_or_debug_text(self):
        async with mounted(InitialScreen, gs_initial()) as pilot:
            assert not has_text(pilot, "Elapsed")
            assert not has_text(pilot, "Checked:")
