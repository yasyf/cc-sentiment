from __future__ import annotations

from textual.widgets import Button, Input

from cc_sentiment.onboarding import GistTimeout, Stage, State as GlobalState
from cc_sentiment.onboarding.state import Identity
from cc_sentiment.onboarding.ui.screens import GistTroubleScreen

from .conftest import fake_caps, has_text, mounted


def gs_gist_trouble(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.TROUBLE,
        trouble=GistTimeout(),
        identity=Identity(github_username=username),
    )


class TestGistTroubleScreen:
    """Strict codification of gist_trouble.py — inline username edit; no restart."""

    async def test_title(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render())
                == "Still watching for your gist"
            )

    async def test_body_explains_typo_likely(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            body = str(pilot.app.screen.query_one("#body").render())
            assert "GitHub usually takes a moment" in body
            assert "username" in body
            assert "never find it" in body

    async def test_username_input_prefilled(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble("alice")) as pilot:
            inp = pilot.app.screen.query_one("#username-input", Input)
            assert inp.value == "alice"

    async def test_submit_button(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            btn = pilot.app.screen.query_one("#submit-btn", Button)
            assert btn.label.plain == "Try this username"

    async def test_email_link_present_when_gpg_available(self):
        async with mounted(
            GistTroubleScreen, gs_gist_trouble(), fake_caps(has_gpg=True)
        ) as pilot:
            link = pilot.app.screen.query_one("#email-link")
            assert "Use email instead" in str(getattr(link, "renderable", link.label.plain))

    async def test_email_link_absent_when_no_gpg(self):
        async with mounted(
            GistTroubleScreen, gs_gist_trouble(), fake_caps(has_gpg=False)
        ) as pilot:
            assert not pilot.app.screen.query("#email-link")

    async def test_no_restart_button(self):
        # Plan: "NO restart link" — restart belongs to VerifyTrouble only.
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            assert not pilot.app.screen.query("#restart-btn")
            assert not has_text(pilot, "Restart setup")

    async def test_no_keep_watching_button(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            assert not has_text(pilot, "Keep watching")

    async def test_no_try_a_different_way(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            assert not has_text(pilot, "try a different way")

    async def test_no_retry_counter(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            # Plan: "no retry counter, no scary error text".
            assert not has_text(pilot, "Attempt ")
            assert not has_text(pilot, "of 3")
            assert not has_text(pilot, "Retry #")

    async def test_rate_limit_note_hidden_on_initial_render(self):
        async with mounted(GistTroubleScreen, gs_gist_trouble()) as pilot:
            note = pilot.app.screen.query("#rate-limit-note")
            assert not note or not note[0].display
