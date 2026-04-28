from __future__ import annotations

from pathlib import Path

from textual.widgets import Button

from cc_sentiment.models import (
    ContributorId,
    GistConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.state import Identity
from cc_sentiment.onboarding.ui.screens import DoneScreen

from .conftest import has_text, mounted


def gs_done_via_gist(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(github_username=username),
        verified_config=GistConfig(
            contributor_id=ContributorId(username),
            key_path=Path.home() / ".cc-sentiment" / "keys" / "id_ed25519",
            gist_id="abcdef1234567890abcd",
        ),
    )


def gs_done_via_github_ssh(username: str = "alice") -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(github_username=username),
        verified_config=SSHConfig(
            contributor_id=ContributorId(username),
            key_path=Path.home() / ".ssh" / "id_ed25519",
        ),
    )


def gs_done_via_gpg() -> GlobalState:
    return GlobalState(
        stage=Stage.DONE,
        identity=Identity(email="alice@example.com", email_usable=True),
        verified_config=GPGConfig(
            contributor_type="gpg",
            contributor_id=ContributorId("DEADBEEFCAFE0001"),
            fpr="DEADBEEFCAFE0001",
        ),
    )


class TestDoneScreen:
    """Strict codification of done.py — verification card + payload card + start."""

    async def test_title(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            assert str(pilot.app.screen.query_one("#title").render()) == "All set"

    async def test_verification_card_present(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            card = pilot.app.screen.query_one("#verification-card")
            assert card.border_title == "Verification"

    async def test_payload_card_present(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            assert pilot.app.screen.query_one("#payload-card") is not None

    async def test_payload_exclusion_line(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            excl = pilot.app.screen.query_one("#payload-exclusion")
            text = str(excl.render())
            assert "No transcript text" in text
            assert "tool inputs" in text
            assert "code" in text

    async def test_payload_card_shows_json_sample(self):
        from rich.syntax import Syntax
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            payload = pilot.app.screen.query_one("#payload-sample")
            content = payload._Static__content  # type: ignore[attr-defined]
            assert isinstance(content, Syntax)
            for key in ("time", "sentiment_score", "claude_model", "turn_count", "tool_calls_per_turn", "read_edit_ratio"):
                assert key in content.code, f"payload card missing {key!r}"

    async def test_start_processing_button(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            btn = pilot.app.screen.query_one("#start-btn", Button)
            assert btn.label.plain == "Start processing"

    async def test_only_one_action(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            assert len(pilot.app.screen.query(Button)) == 1

    async def test_no_advanced_settings_link(self):
        async with mounted(DoneScreen, gs_done_via_gist()) as pilot:
            assert not has_text(pilot, "advanced settings")
            assert not has_text(pilot, "Edit")

    async def test_verification_via_gist_says_via_public_gist(self):
        async with mounted(DoneScreen, gs_done_via_gist("alice")) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").render())
            assert "@alice" in line
            assert "via public gist" in line

    async def test_verification_existing_ssh_says_on_github(self):
        async with mounted(DoneScreen, gs_done_via_github_ssh("alice")) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").render())
            assert "@alice" in line
            assert "on GitHub" in line

    async def test_verification_gpg_uses_fpr_short(self):
        async with mounted(DoneScreen, gs_done_via_gpg()) as pilot:
            line = str(pilot.app.screen.query_one("#verification-line").render())
            # fpr[-8:] for DEADBEEFCAFE0001 is "CAFE0001"
            assert "CAFE0001" in line
