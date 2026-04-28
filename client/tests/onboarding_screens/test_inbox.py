from __future__ import annotations

from textual.widgets import Button, ProgressBar

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import Identity
from cc_sentiment.onboarding.ui.screens import InboxScreen

from .conftest import has_text, mounted


def gs_inbox(email: str = "alice@example.com") -> GlobalState:
    return GlobalState(
        stage=Stage.INBOX,
        identity=Identity(email=email, email_usable=True),
    )


class TestInboxScreen:
    """Strict codification of inbox.py — passive polling, no primary action."""

    async def test_title_says_check_inbox(self):
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render()) == "Check your inbox"
            )

    async def test_body_includes_email_address(self):
        async with mounted(InboxScreen, gs_inbox("alice@example.com")) as pilot:
            body = str(pilot.app.screen.query_one("#body").render())
            assert "alice@example.com" in body
            assert "Open it" in body

    async def test_polling_status_present(self):
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            status = pilot.app.screen.query_one("#polling-status")
            assert "Waiting for verification" in str(status.render())

    async def test_no_primary_button(self):
        # Plan: "NONE primary. The screen passively polls". Stricter: no buttons
        # at all on initial render — secondary links appear only after delay.
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            assert not pilot.app.screen.query(Button)

    async def test_no_resend_or_reopen_buttons(self):
        # Plan: "no Reopen verification or resend-primary behavior".
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            assert not has_text(pilot, "Reopen verification")
            assert not has_text(pilot, "Resend")

    async def test_secondary_links_hidden_initially(self):
        # Plan: "After a polite delay (~60s), two quiet secondary links appear".
        # On initial render, neither should be visible.
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            different = pilot.app.screen.query("#different-email-link")
            recheck = pilot.app.screen.query("#recheck-link")
            assert not different or not different[0].display
            assert not recheck or not recheck[0].display

    async def test_no_progress_bar(self):
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            assert not pilot.app.screen.query(ProgressBar)

    async def test_no_elapsed_seconds(self):
        # Plan: "No explicit progress bar; no 'X seconds elapsed'".
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            assert not has_text(pilot, "seconds elapsed")
            assert not has_text(pilot, "Elapsed")

    async def test_rate_limit_note_hidden_by_default(self):
        async with mounted(InboxScreen, gs_inbox()) as pilot:
            note = pilot.app.screen.query("#rate-limit-note")
            assert not note or not note[0].display
