from __future__ import annotations

from textual.widgets import Button

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import SavedRetryScreen

from .conftest import has_text, mounted


def gs_saved_retry() -> GlobalState:
    return GlobalState(stage=Stage.SAVED_RETRY, has_saved_config=True)


class TestSavedRetryScreen:
    """Strict codification of saved_retry.py — small recovery card; no error tone."""

    async def test_title_says_couldnt_reach_sentiments(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            title = pilot.app.screen.query_one("#title")
            assert str(title.render()) == "Couldn't reach sentiments.cc"

    async def test_body_reassures_will_try_again(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            body = pilot.app.screen.query_one("#body")
            assert "try again" in str(body.render()).lower()

    async def test_retry_button_focused(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            btn = pilot.app.screen.query_one("#retry-btn", Button)
            assert btn.label.plain == "Retry"
            assert btn.has_focus

    async def test_restart_link_present(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            link = pilot.app.screen.query_one("#restart-link")
            assert "Set up again" in str(getattr(link, "renderable", link.label.plain))

    async def test_only_two_actions(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            buttons = pilot.app.screen.query(Button)
            assert len(buttons) <= 2

    async def test_no_technical_auth_dump(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            assert not has_text(pilot, "401")
            assert not has_text(pilot, "403")
            assert not has_text(pilot, "stack")
            assert not has_text(pilot, "Traceback")

    async def test_no_error_codes(self):
        async with mounted(SavedRetryScreen, gs_saved_retry()) as pilot:
            assert not has_text(pilot, "code:")
            assert not has_text(pilot, "ERROR")
