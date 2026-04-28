from __future__ import annotations

from textual.widgets import Button

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui.screens import BlockedScreen

from .conftest import fake_caps, has_text, mounted


def gs_blocked() -> GlobalState:
    return GlobalState(stage=Stage.BLOCKED)


class TestBlockedScreen:
    """Strict codification of blocked.py — install hint, no error tone."""

    async def test_title(self):
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            assert (
                str(pilot.app.screen.query_one("#title").render())
                == "We need an SSH client or GPG"
            )

    async def test_body_explains_and_instructs(self):
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            body = str(pilot.app.screen.query_one("#body").render())
            assert "install" in body
            assert "cc-sentiment setup" in body

    async def test_install_hint_brew_when_brew_available(self):
        async with mounted(BlockedScreen, gs_blocked(), fake_caps(has_brew=True)) as pilot:
            hint = pilot.app.screen.query_one("#install-hint")
            text = str(hint.render())
            assert "brew install" in text
            assert "gnupg" in text

    async def test_install_guide_target_is_ssh_docs_when_no_ssh_keygen(self):
        # Plan: "SSH if neither is present, GPG if SSH is present and only the
        # GPG path was unavailable."
        async with mounted(
            BlockedScreen, gs_blocked(), fake_caps(has_ssh_keygen=False, has_gpg=False)
        ) as pilot:
            btn = pilot.app.screen.query_one("#install-guide-btn")
            target = (
                getattr(btn, "url", None)
                or btn.classes
                or set()
            )
            assert "ssh" in str(target).lower()

    async def test_install_guide_target_is_gpg_docs_when_ssh_present(self):
        async with mounted(
            BlockedScreen, gs_blocked(), fake_caps(has_ssh_keygen=True, has_gpg=False)
        ) as pilot:
            btn = pilot.app.screen.query_one("#install-guide-btn")
            target = (
                getattr(btn, "url", None)
                or btn.classes
                or set()
            )
            assert "gpg" in str(target).lower()

    async def test_install_hint_generic_when_no_brew(self):
        async with mounted(BlockedScreen, gs_blocked(), fake_caps(has_brew=False)) as pilot:
            hint = pilot.app.screen.query_one("#install-hint")
            text = str(hint.render())
            assert "Install OpenSSH or GPG" in text

    async def test_install_guide_button(self):
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            btn = pilot.app.screen.query_one("#install-guide-btn", Button)
            assert btn.label.plain == "Open install guide"

    async def test_quit_button(self):
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            btn = pilot.app.screen.query_one("#quit-btn", Button)
            assert btn.label.plain == "Quit"

    async def test_only_two_buttons(self):
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            assert len(pilot.app.screen.query(Button)) == 2

    async def test_no_scary_red_error_ui(self):
        # Plan: "No big red error UI, no 'we can't help you' tone".
        async with mounted(BlockedScreen, gs_blocked()) as pilot:
            assert not has_text(pilot, "ERROR")
            assert not has_text(pilot, "FATAL")
            assert not has_text(pilot, "can't help")
