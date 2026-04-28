from __future__ import annotations

from textual.widgets import Button, DataTable, Input

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import UserFormScreen

from .conftest import has_text, mounted


def gs_user_form() -> GlobalState:
    return GlobalState(stage=Stage.USER_FORM)


class TestUserFormScreen:
    """Strict codification of user_form.py — last-resort username form."""

    async def test_title_is_the_question(self):
        # Plan: "the title IS the question" — no body paragraph above input.
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            title = pilot.app.screen.query_one("#title")
            assert str(title.renderable) == "What's your GitHub username?"

    async def test_no_body_paragraph(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            assert not pilot.app.screen.query("#body")

    async def test_username_input_present_and_focused(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            inp = pilot.app.screen.query_one("#username-input", Input)
            assert inp.has_focus

    async def test_username_input_placeholder_is_yasyf(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            inp = pilot.app.screen.query_one("#username-input", Input)
            assert inp.placeholder == "yasyf"

    async def test_continue_button_present(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            btn = pilot.app.screen.query_one("#continue-btn", Button)
            assert btn.label.plain == "Continue"

    async def test_no_github_link_present_and_quiet(self):
        # Plan: "Quiet I don't use GitHub →" — present here per docstring.
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            link = pilot.app.screen.query_one("#no-github-link")
            assert "I don't use GitHub" in str(getattr(link, "renderable", link.label.plain))

    async def test_no_github_link_is_muted(self):
        # Plan: "I don't use GitHub → is muted; only colored on hover".
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            link = pilot.app.screen.query_one("#no-github-link")
            assert "muted" in (link.classes or set())

    async def test_only_one_primary_button(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            buttons = pilot.app.screen.query(Button)
            primary = [b for b in buttons if b.id == "continue-btn"]
            assert len(primary) == 1

    async def test_no_status_line_on_initial_render(self):
        # Status only appears for validating / errors — not on first render.
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            status = pilot.app.screen.query("#status")
            assert not status or not str(status[0].renderable).strip()

    async def test_no_table(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            assert not pilot.app.screen.query(DataTable)

    async def test_no_progress_bar(self):
        # Plan: no tables, no progress bars, no debug.
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            from textual.widgets import ProgressBar
            assert not pilot.app.screen.query(ProgressBar)

    async def test_no_debug_text(self):
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            assert not has_text(pilot, "DEBUG")
            assert not has_text(pilot, "Elapsed")

    async def test_no_error_messages_on_initial_render(self):
        # Error / validating states must not leak into the calm initial render.
        async with mounted(UserFormScreen, gs_user_form()) as pilot:
            assert not has_text(pilot, "wasn't found")
            assert not has_text(pilot, "Couldn't reach GitHub")
            assert not has_text(pilot, "Validating")
