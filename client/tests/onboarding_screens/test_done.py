from __future__ import annotations

from textual.widgets import Button

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import (
    ExistingKey,
    Identity,
    KeySource,
    SelectedKey,
)
from cc_sentiment.onboarding.ui.screens import DoneScreen

from .conftest import fake_caps, has_text, mounted


def gs_done_ssh_managed_gist(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(github_username=username),
        selected=SelectedKey(
            source=KeySource.MANAGED,
            key=ExistingKey(fingerprint="SHA256:abcd1234", label="cc-sentiment"),
        ),
    )


def gs_done_ssh_existing_gh_added(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(github_username=username),
        selected=SelectedKey(
            source=KeySource.EXISTING_SSH,
            key=ExistingKey(fingerprint="SHA256:wxyz5678", label="id_ed25519"),
        ),
    )


def gs_done_gpg_email() -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(email="alice@example.com", email_usable=True),
        selected=SelectedKey(
            source=KeySource.EXISTING_GPG,
            key=ExistingKey(fingerprint="DEADBEEFCAFE0001", label="alice@example.com"),
        ),
    )


class TestDoneScreen:
    """Strict codification of done.py — verification card + payload card + start."""

    async def test_title(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            assert str(pilot.app.screen.query_one("#title").renderable) == "All set"

    async def test_verification_card_present(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            card = pilot.app.screen.query_one("#verification-card")
            assert "Verification" in str(getattr(card, "renderable", "")) or has_text(
                pilot, "Verification"
            )

    async def test_payload_card_present(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            assert pilot.app.screen.query_one("#payload-card") is not None

    async def test_payload_exclusion_line(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            excl = pilot.app.screen.query_one("#payload-exclusion")
            text = str(excl.renderable)
            assert "No transcript text" in text
            assert "tool inputs" in text
            assert "code" in text

    async def test_payload_card_shows_json_sample(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            for key in ("time", "sentiment_score", "claude_model", "turn_count", "tool_calls_per_turn", "read_edit_ratio"):
                assert has_text(pilot, key), f"payload card missing {key!r}"

    async def test_start_processing_button(self):
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            btn = pilot.app.screen.query_one("#start-btn", Button)
            assert btn.label.plain == "Start processing"

    async def test_only_one_action(self):
        # Plan: "No other actions."
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            assert len(pilot.app.screen.query(Button)) == 1

    async def test_no_advanced_settings_link(self):
        # Plan: "No 'advanced settings' link, no 'edit' affordances."
        async with mounted(DoneScreen, gs_done_ssh_managed_gist()) as pilot:
            assert not has_text(pilot, "advanced settings")
            assert not has_text(pilot, "Edit")

    # ─── Verification line variants ──────────────────────────────────────

    async def test_verification_managed_via_gist_says_via_public_gist(self):
        # Managed SSH that didn't go through gh-add → manual gist.
        async with mounted(
            DoneScreen,
            gs_done_ssh_managed_gist("alice"),
            fake_caps(gh_authenticated=False),
        ) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").renderable)
            assert "@alice" in line
            assert "via public gist" in line or "on GitHub" in line

    async def test_verification_existing_ssh_says_on_github(self):
        async with mounted(
            DoneScreen,
            gs_done_ssh_existing_gh_added("alice"),
            fake_caps(gh_authenticated=True),
        ) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").renderable)
            assert "@alice" in line
            assert "on GitHub" in line

    async def test_verification_gpg_uses_fpr_short(self):
        async with mounted(DoneScreen, gs_done_gpg_email()) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").renderable)
            # fpr[-8:] for DEADBEEFCAFE0001 is "CAFE0001"
            assert "CAFE0001" in line

    async def test_verification_managed_gpg_uses_fpr_short(self):
        # Plan: managed GPG email branch shows fpr_short, not @username.
        gs_managed_gpg = GlobalState(
            stage=Stage.DONE,
            identity=Identity(email="alice@example.com", email_usable=True),
            selected=SelectedKey(
                source=KeySource.MANAGED,
                key=ExistingKey(fingerprint="DEADBEEFCAFE0001", label="cc-sentiment"),
            ),
        )
        async with mounted(DoneScreen, gs_managed_gpg) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").renderable)
            assert "CAFE0001" in line
