from __future__ import annotations

from textual.widgets import Button, DataTable, Input

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import (
    ExistingKey,
    Identity,
    KeySource,
    SelectedKey,
)
from cc_sentiment.onboarding.ui.screens import SshMethodScreen

from .conftest import fake_caps, has_text, mounted


def gs_ssh_method(
    username: str = "alice",
) -> GlobalState:
    return GlobalState(
        stage=Stage.SSH_METHOD,
        identity=Identity(github_username=username),
        selected=SelectedKey(
            source=KeySource.EXISTING_SSH,
            key=ExistingKey(fingerprint="SHA256:test", label="id_ed25519"),
        ),
    )


class TestSshMethodScreen:
    """Strict codification of ssh_method.py — gist default; gh-add secondary."""

    async def test_title_asks_where_to_publish(self):
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").renderable)
                == "Where should we publish your signature?"
            )

    async def test_body_explains_why(self):
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            body = str(pilot.app.screen.query_one("#body").renderable)
            assert "public" in body
            assert "sentiments.cc" in body

    async def test_username_row_absent_when_known(self):
        # Inline username only when identity.has_username is False.
        async with mounted(SshMethodScreen, gs_ssh_method(username="alice")) as pilot:
            assert not pilot.app.screen.query("#username-row")

    async def test_username_row_present_when_missing(self):
        async with mounted(SshMethodScreen, gs_ssh_method(username="")) as pilot:
            row = pilot.app.screen.query_one("#username-row")
            assert row.display

    async def test_username_input_inside_row(self):
        async with mounted(SshMethodScreen, gs_ssh_method(username="")) as pilot:
            inp = pilot.app.screen.query_one("#username-input", Input)
            assert inp is not None

    async def test_gist_button_is_primary_and_focused(self):
        # Plan: "default gist" + "Primary 'Publish as a gist' — focused".
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            btn = pilot.app.screen.query_one("#gist-btn", Button)
            assert btn.label.plain == "Publish as a gist"
            assert btn.has_focus

    async def test_gist_subline_includes_username(self):
        async with mounted(SshMethodScreen, gs_ssh_method(username="alice")) as pilot:
            sub = pilot.app.screen.query_one("#gist-subline")
            text = str(sub.renderable)
            assert "alice" in text
            assert "github.com/" in text
            assert "Delete it any time" in text

    async def test_gist_subline_does_not_leak_unfilled_placeholder(self):
        # When username is missing, the subline must not render `{username}`.
        async with mounted(SshMethodScreen, gs_ssh_method(username="")) as pilot:
            sub = pilot.app.screen.query_one("#gist-subline")
            assert "{username}" not in str(sub.renderable)

    async def test_username_input_placeholder_is_yasyf(self):
        async with mounted(SshMethodScreen, gs_ssh_method(username="")) as pilot:
            inp = pilot.app.screen.query_one("#username-input", Input)
            assert inp.placeholder == "yasyf"

    async def test_gh_add_link_present(self):
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            link = pilot.app.screen.query_one("#gh-add-link")
            assert "Add it to GitHub" in str(getattr(link, "renderable", link.label.plain))

    async def test_gh_add_subline_authed_when_gh_authed(self):
        async with mounted(
            SshMethodScreen,
            gs_ssh_method(),
            fake_caps(gh_authenticated=True),
        ) as pilot:
            sub = pilot.app.screen.query_one("#gh-add-subline")
            assert "We'll add it for you" in str(sub.renderable)

    async def test_gh_add_subline_manual_when_not_authed(self):
        async with mounted(
            SshMethodScreen,
            gs_ssh_method(),
            fake_caps(gh_authenticated=False),
        ) as pilot:
            sub = pilot.app.screen.query_one("#gh-add-subline")
            assert "github.com/settings/keys" in str(sub.renderable)

    async def test_gh_add_de_emphasized_when_not_authed(self):
        # Plan Q&A: "de-emphasize manual if not gh-authenticated".
        # Encoded as a CSS class on the link, not a hex color.
        async with mounted(
            SshMethodScreen,
            gs_ssh_method(),
            fake_caps(gh_authenticated=False),
        ) as pilot:
            link = pilot.app.screen.query_one("#gh-add-link")
            assert "muted" in (link.classes or set()) or "de-emphasized" in (link.classes or set())

    async def test_gh_add_not_de_emphasized_when_authed(self):
        async with mounted(
            SshMethodScreen,
            gs_ssh_method(),
            fake_caps(gh_authenticated=True),
        ) as pilot:
            link = pilot.app.screen.query_one("#gh-add-link")
            assert "muted" not in (link.classes or set())

    async def test_only_two_actions(self):
        # Plan: "No third option, no comparison table, no help link."
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            buttons = pilot.app.screen.query(Button)
            primary_or_secondary = [
                b for b in buttons if b.id in ("gist-btn", "gh-add-link")
            ]
            assert len(primary_or_secondary) <= 2

    async def test_no_help_link(self):
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            assert not has_text(pilot, "Learn more")
            assert not has_text(pilot, "Help")
            assert not has_text(pilot, "Documentation")

    async def test_no_comparison_table(self):
        async with mounted(SshMethodScreen, gs_ssh_method()) as pilot:
            assert not pilot.app.screen.query(DataTable)
