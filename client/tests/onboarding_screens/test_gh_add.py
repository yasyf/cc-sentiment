from __future__ import annotations

from textual.widgets import Button, Static

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import (
    ExistingKey,
    Identity,
    KeySource,
    SelectedKey,
)
from cc_sentiment.onboarding.ui.screens import GhAddScreen

from .conftest import fake_caps, has_text, mounted


def gs_gh_add(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.GH_ADD,
        identity=Identity(github_username=username),
        selected=SelectedKey(
            source=KeySource.EXISTING_SSH,
            key=ExistingKey(fingerprint="SHA256:test", label="id_ed25519"),
        ),
    )


class TestGhAddScreenAuto:
    """gh-authed flavor — silent automatic, like Working."""

    async def test_title_is_adding_to_github(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=True)
        ) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render()) == "Adding to GitHub…"
            )

    async def test_status_line_present(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=True)
        ) as pilot:
            status = pilot.app.screen.query_one("#status", Static)
            assert "Adding your signature" in str(status.render())

    async def test_no_buttons_in_auto_flavor(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=True)
        ) as pilot:
            assert not pilot.app.screen.query(Button)

    async def test_no_key_preview_in_auto_flavor(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=True)
        ) as pilot:
            assert not pilot.app.screen.query("#key-preview")


class TestGhAddScreenManual:
    """no-gh flavor — manual paste with prominent fallback."""

    async def test_title_is_add_signature_to_github(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render())
                == "Add your signature to GitHub"
            )

    async def test_body_explains_paste(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            body = str(pilot.app.screen.query_one("#body").render())
            assert "github.com/settings/keys" in body

    async def test_key_preview_present_in_manual_flavor(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            preview = pilot.app.screen.query_one("#key-preview")
            assert preview is not None

    async def test_open_github_button(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            btn = pilot.app.screen.query_one("#open-btn", Button)
            assert btn.label.plain == "Open GitHub"

    async def test_copy_again_link(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            link = pilot.app.screen.query_one("#copy-again-link")
            assert "Copy again" in str(getattr(link, "renderable", link.label.plain))

    async def test_watcher_row_present(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            watcher = pilot.app.screen.query_one("#watcher-row")
            assert "GitHub" in str(watcher.render())


class TestGhAddScreenForbidden:
    async def test_no_advanced_toggle(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            assert not has_text(pilot, "Advanced")
            assert not has_text(pilot, "Switch mode")


class TestGhAddRateLimit:
    async def test_rate_limit_note_hidden_on_initial_render(self):
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            note = pilot.app.screen.query("#rate-limit-note")
            assert not note or not note[0].display


class TestGhAddFallbackPanel:
    async def test_fallback_panel_absent_by_default(self):
        # Plan: only appears when both clipboard and browser fail.
        async with mounted(
            GhAddScreen, gs_gh_add(), fake_caps(gh_authenticated=False)
        ) as pilot:
            assert not pilot.app.screen.query("#fallback-panel")
            assert not pilot.app.screen.query("#fallback-confirm-btn")
