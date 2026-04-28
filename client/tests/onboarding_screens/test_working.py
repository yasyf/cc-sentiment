from __future__ import annotations

from textual.widgets import Button, DataTable, ProgressBar

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import WorkingScreen

from .conftest import has_text, mounted


def gs_working() -> GlobalState:
    return GlobalState(stage=Stage.WORKING)


class TestWorkingScreen:
    """Strict codification of working.py — spinner only, one status line."""

    async def test_title_renders(self):
        async with mounted(WorkingScreen, gs_working()) as pilot:
            assert str(pilot.app.screen.query_one("#title").render()) == "Setting up…"

    async def test_status_line_present(self):
        async with mounted(WorkingScreen, gs_working()) as pilot:
            status = pilot.app.screen.query_one("#status")
            assert "Creating your signature" in str(status.render())

    async def test_no_buttons(self):
        # Plan: "NONE. The screen has no buttons".
        async with mounted(WorkingScreen, gs_working()) as pilot:
            assert not pilot.app.screen.query(Button)

    async def test_no_progress_bar(self):
        async with mounted(WorkingScreen, gs_working()) as pilot:
            assert not pilot.app.screen.query(ProgressBar)

    async def test_no_checklist(self):
        async with mounted(WorkingScreen, gs_working()) as pilot:
            assert not pilot.app.screen.query(DataTable)
            assert not has_text(pilot, "✓")
            assert not has_text(pilot, "[x]")

    async def test_no_elapsed_or_cancel(self):
        async with mounted(WorkingScreen, gs_working()) as pilot:
            assert not has_text(pilot, "Elapsed")
            assert not has_text(pilot, "Cancel")

    async def test_set_status_updates_line(self):
        # Plan: "Status line cycles through" three messages as work progresses.
        async with mounted(WorkingScreen, gs_working()) as pilot:
            view = pilot.app.screen
            view.set_status("Creating GitHub gist…")
            await pilot.pause()
            status = view.query_one("#status")
            assert "Creating GitHub gist" in str(status.render())
            view.set_status("Verifying…")
            await pilot.pause()
            assert "Verifying" in str(view.query_one("#status").render())
