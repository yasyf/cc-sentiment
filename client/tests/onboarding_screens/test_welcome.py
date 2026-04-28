from __future__ import annotations

from textual.widgets import Button, DataTable

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import WelcomeScreen

from .conftest import has_text, mounted


def gs_welcome(has_saved_config: bool = False) -> GlobalState:
    return GlobalState(stage=Stage.WELCOME, has_saved_config=has_saved_config)


class TestWelcomeScreen:
    """Strict codification of welcome.py — calm entry, one obvious action."""

    async def test_title_renders(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            title = pilot.app.screen.query_one("#title")
            assert str(title.renderable) == "Set up cc-sentiment"

    async def test_body_explains_signature_and_duration(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            body = str(pilot.app.screen.query_one("#body").renderable)
            assert "signature" in body
            assert "30 seconds" in body
            assert "confirm uploads" in body

    async def test_get_started_button_always_visible(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            btn = pilot.app.screen.query_one("#get-started-btn", Button)
            assert btn.label.plain == "Get started"

    async def test_get_started_button_visible_even_when_checking(self):
        # Plan: "Welcome always shows Get started; checking status is separate and subtle"
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            btn = pilot.app.screen.query_one("#get-started-btn", Button)
            assert btn.display

    async def test_checking_status_present(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            status = pilot.app.screen.query_one("#checking-status")
            assert "Checking your setup" in str(status.renderable)

    async def test_saved_invalid_line_absent_when_fresh(self):
        async with mounted(WelcomeScreen, gs_welcome(has_saved_config=False)) as pilot:
            assert not pilot.app.screen.query("#saved-invalid-line")

    async def test_saved_invalid_line_present_when_has_saved_config(self):
        async with mounted(WelcomeScreen, gs_welcome(has_saved_config=True)) as pilot:
            line = pilot.app.screen.query_one("#saved-invalid-line")
            assert "needs refreshing" in str(line.renderable)

    async def test_saved_invalid_line_appears_above_body(self):
        # Docstring: "One extra muted line ABOVE the body".
        async with mounted(WelcomeScreen, gs_welcome(has_saved_config=True)) as pilot:
            screen = pilot.app.screen
            widgets = list(screen.walk_children())
            ids = [w.id for w in widgets if w.id in {"saved-invalid-line", "body"}]
            assert ids.index("saved-invalid-line") < ids.index("body")

    async def test_no_no_github_link(self):
        # "I don't use GitHub →" lives only on UserForm and Publish, not Welcome.
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            assert not pilot.app.screen.query("#no-github-link")
            assert not has_text(pilot, "I don't use GitHub")

    async def test_no_table_of_detected_tools(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            assert not pilot.app.screen.query(DataTable)

    async def test_no_debug_rows(self):
        async with mounted(WelcomeScreen, gs_welcome()) as pilot:
            assert not has_text(pilot, "Detected:")
            assert not has_text(pilot, "Checked:")
            assert not has_text(pilot, "DEBUG")
