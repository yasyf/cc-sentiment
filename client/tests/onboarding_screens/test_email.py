from __future__ import annotations

from textual.widgets import Button, Input

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import Identity
from cc_sentiment.onboarding.ui.screens import EmailScreen

from .conftest import has_text, mounted


def gs_email(
    email: str = "",
    email_usable: bool = False,
) -> GlobalState:
    return GlobalState(
        stage=Stage.EMAIL,
        identity=Identity(email=email, email_usable=email_usable),
    )


class TestEmailScreen:
    """Strict codification of email.py — one field, one button."""

    async def test_title_asks_for_email(self):
        async with mounted(EmailScreen, gs_email()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render())
                == "What email should we use?"
            )

    async def test_body_explains_one_time_link(self):
        async with mounted(EmailScreen, gs_email()) as pilot:
            body = str(pilot.app.screen.query_one("#body").render())
            assert "one-time" in body
            assert "verification" in body

    async def test_email_input_present_and_focused(self):
        async with mounted(EmailScreen, gs_email()) as pilot:
            inp = pilot.app.screen.query_one("#email-input", Input)
            assert inp.has_focus

    async def test_email_input_blank_when_no_usable_email(self):
        async with mounted(EmailScreen, gs_email(email_usable=False)) as pilot:
            inp = pilot.app.screen.query_one("#email-input", Input)
            assert inp.value == ""

    async def test_email_input_blank_when_email_set_but_not_usable(self):
        # Plan: only pre-fill when `email_usable`. A noreply / outdated address
        # should NOT auto-fill into the input.
        async with mounted(
            EmailScreen,
            gs_email(email="alice@users.noreply.github.com", email_usable=False),
        ) as pilot:
            inp = pilot.app.screen.query_one("#email-input", Input)
            assert inp.value == ""

    async def test_email_input_prefilled_when_usable(self):
        async with mounted(
            EmailScreen,
            gs_email(email="alice@example.com", email_usable=True),
        ) as pilot:
            inp = pilot.app.screen.query_one("#email-input", Input)
            assert inp.value == "alice@example.com"

    async def test_send_link_button(self):
        async with mounted(EmailScreen, gs_email()) as pilot:
            btn = pilot.app.screen.query_one("#send-btn", Button)
            assert btn.label.plain == "Send link"

    async def test_only_one_action(self):
        # Plan: "One field, one button. No 'use a different email server' toggle."
        async with mounted(EmailScreen, gs_email()) as pilot:
            buttons = pilot.app.screen.query(Button)
            assert len(buttons) == 1

    async def test_no_no_github_link(self):
        # Plan: "I don't use GitHub link does NOT appear here — this is the GPG branch."
        async with mounted(EmailScreen, gs_email()) as pilot:
            assert not pilot.app.screen.query("#no-github-link")
            assert not has_text(pilot, "I don't use GitHub")

    async def test_no_gpg_or_openpgp_in_user_facing_text(self):
        # Plan: "Words 'GPG' / 'OpenPGP' never appear in the user-facing copy."
        async with mounted(EmailScreen, gs_email()) as pilot:
            assert not has_text(pilot, "GPG")
            assert not has_text(pilot, "OpenPGP")

    async def test_no_pgp_explainer(self):
        async with mounted(EmailScreen, gs_email()) as pilot:
            assert not has_text(pilot, "PGP")
            assert not has_text(pilot, "encrypt")

    async def test_no_keys_openpgp_org_in_initial_render(self):
        # The "Couldn't reach keys.openpgp.org" string is an error-state hint;
        # it must NOT appear on the calm initial render.
        async with mounted(EmailScreen, gs_email()) as pilot:
            assert not has_text(pilot, "keys.openpgp.org")
