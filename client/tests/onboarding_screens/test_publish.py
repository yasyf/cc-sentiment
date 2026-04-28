from __future__ import annotations

from textual.widgets import Button, DataTable, Input

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import (
    ExistingKey,
    Identity,
    KeySource,
    SelectedKey,
)
from cc_sentiment.onboarding.ui.screens import PublishScreen

from .conftest import fake_caps, has_text, mounted


def gs_publish(
    username: str = "alice",
    resumed: bool = False,
) -> GlobalState:
    return GlobalState(
        stage=Stage.PUBLISH,
        identity=Identity(github_username=username),
        selected=SelectedKey(
            source=KeySource.MANAGED,
            key=ExistingKey(fingerprint="SHA256:test", label="cc-sentiment"),
        ),
        resumed_from_pending=resumed,
    )


class TestPublishScreen:
    """Strict codification of publish.py — manual gist publish; resume aware."""

    async def test_title(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            assert str(pilot.app.screen.query_one("#title").renderable) == "One more step"

    async def test_body_explains_clipboard_and_auto_find(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            body = str(pilot.app.screen.query_one("#body").renderable)
            assert "clipboard" in body
            assert "automatically" in body

    async def test_key_preview_present(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            preview = pilot.app.screen.query_one("#key-preview")
            assert preview is not None

    async def test_open_github_button_is_primary(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            btn = pilot.app.screen.query_one("#open-btn", Button)
            assert btn.label.plain == "Open GitHub"

    async def test_copy_again_link_present(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            link = pilot.app.screen.query_one("#copy-again-link")
            assert "Copy again" in str(getattr(link, "renderable", link.label.plain))

    async def test_no_github_link_present_when_gpg_available(self):
        async with mounted(
            PublishScreen, gs_publish(), fake_caps(has_gpg=True)
        ) as pilot:
            link = pilot.app.screen.query_one("#no-github-link")
            assert "I don't use GitHub" in str(getattr(link, "renderable", link.label.plain))

    async def test_no_github_link_absent_when_no_gpg(self):
        async with mounted(
            PublishScreen, gs_publish(), fake_caps(has_gpg=False)
        ) as pilot:
            assert not pilot.app.screen.query("#no-github-link")

    async def test_watcher_row_present(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            watcher = pilot.app.screen.query_one("#watcher-row")
            assert "Watching for your gist" in str(watcher.renderable)

    async def test_no_url_input(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            inputs = [i for i in pilot.app.screen.query(Input) if i.id != "username-input"]
            assert not inputs

    async def test_no_check_now(self):
        # Plan forbidden term: "Check now".
        async with mounted(PublishScreen, gs_publish()) as pilot:
            assert not has_text(pilot, "Check now")

    async def test_no_elapsed(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            assert not has_text(pilot, "Elapsed")

    async def test_no_debug_table(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            assert not pilot.app.screen.query(DataTable)

    async def test_open_github_button_targets_gist_new_url(self):
        # Plan: "Primary 'Open GitHub' — calls app.open_url for
        # https://gist.github.com/new."
        async with mounted(PublishScreen, gs_publish()) as pilot:
            btn = pilot.app.screen.query_one("#open-btn", Button)
            url = getattr(btn, "url", "") or ""
            assert "gist.github.com/new" in url

    async def test_rate_limit_note_hidden_on_initial_render(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            note = pilot.app.screen.query("#rate-limit-note")
            assert not note or not note[0].display

    # ─── Fallback panel (clipboard or browser failed) ────────────────────

    async def test_fallback_panel_absent_by_default(self):
        async with mounted(PublishScreen, gs_publish()) as pilot:
            assert not pilot.app.screen.query("#fallback-panel")

    async def test_resume_marks_screen_as_resumed(self):
        # Plan Q&A: "Pending gist resume — Re-copy and reopen" — auto on mount.
        # Surface as a CSS class or data attribute for testability.
        async with mounted(PublishScreen, gs_publish(resumed=True)) as pilot:
            screen = pilot.app.screen
            assert "resumed" in (screen.classes or set())

    async def test_resume_does_not_change_visible_layout(self):
        # User shouldn't notice they were resumed — same widgets present.
        async with mounted(PublishScreen, gs_publish(resumed=True)) as pilot:
            assert pilot.app.screen.query_one("#open-btn")
            assert pilot.app.screen.query_one("#watcher-row")
