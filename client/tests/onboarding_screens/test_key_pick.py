from __future__ import annotations

from textual.widgets import DataTable

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.state import ExistingKey, ExistingKeys
from cc_sentiment.onboarding.ui.screens import KeyPickScreen

from .conftest import fake_caps, has_text, mounted


def ssh(label: str = "id_ed25519", managed: bool = False) -> ExistingKey:
    return ExistingKey(fingerprint=f"SHA256:{label}", label=label, managed=managed)


def gpg(email: str = "alice@example.com") -> ExistingKey:
    return ExistingKey(fingerprint="DEADBEEFCAFE0001", label=email)


def gs_key_pick(
    ssh_keys: tuple[ExistingKey, ...] = (),
    gpg_keys: tuple[ExistingKey, ...] = (),
) -> GlobalState:
    return GlobalState(
        stage=Stage.KEY_PICK,
        existing_keys=ExistingKeys(ssh=ssh_keys, gpg=gpg_keys),
    )


def caps_managed_recommended() -> Capabilities:
    # Plan Q&A: managed is recommended when gh is authed and ssh-keygen present.
    return fake_caps(has_ssh_keygen=True, has_gh=True, gh_authenticated=True)


class TestKeyPickScreen:
    """Strict codification of key_pick.py — big card-based picker, never a table."""

    async def test_title(self):
        async with mounted(KeyPickScreen, gs_key_pick()) as pilot:
            assert str(pilot.app.screen.query_one("#title").renderable) == "Pick your signature"

    async def test_no_table_widget(self):
        # Plan: "Existing-key UI must be large, readable, card-based, and not table-like".
        async with mounted(KeyPickScreen, gs_key_pick(ssh_keys=(ssh(),))) as pilot:
            assert not pilot.app.screen.query(DataTable)

    async def test_managed_card_always_present(self):
        async with mounted(KeyPickScreen, gs_key_pick()) as pilot:
            card = pilot.app.screen.query_one("#managed-card")
            assert card is not None

    async def test_managed_card_label(self):
        async with mounted(KeyPickScreen, gs_key_pick()) as pilot:
            assert has_text(pilot, "Create a new signature")

    async def test_managed_card_subline(self):
        async with mounted(KeyPickScreen, gs_key_pick()) as pilot:
            assert has_text(pilot, "Dedicated to cc-sentiment")
            assert has_text(pilot, "~/.cc-sentiment/keys")

    async def test_one_ssh_card_per_existing_ssh_key(self):
        keys = (ssh("id_ed25519"), ssh("id_rsa"))
        async with mounted(KeyPickScreen, gs_key_pick(ssh_keys=keys)) as pilot:
            cards = pilot.app.screen.query(".key-card")
            # 2 SSH + 0 GPG + 1 managed = 3 (managed has its own ID but also class)
            ssh_cards = [c for c in cards if c.id and c.id.startswith("ssh-card-")]
            assert len(ssh_cards) == 2

    async def test_one_gpg_card_per_existing_gpg_key(self):
        keys = (gpg("a@x.com"), gpg("b@x.com"))
        async with mounted(KeyPickScreen, gs_key_pick(gpg_keys=keys)) as pilot:
            cards = pilot.app.screen.query(".key-card")
            gpg_cards = [c for c in cards if c.id and c.id.startswith("gpg-card-")]
            assert len(gpg_cards) == 2

    async def test_no_existing_keys_only_managed_card(self):
        async with mounted(KeyPickScreen, gs_key_pick()) as pilot:
            cards = pilot.app.screen.query(".key-card")
            existing = [c for c in cards if c.id and (c.id.startswith("ssh-card-") or c.id.startswith("gpg-card-"))]
            assert not existing

    async def test_managed_focused_when_recommended(self):
        # Plan Q&A: "Key choice default — First existing focused unless managed
        # is recommended, then managed focused."
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            caps_managed_recommended(),
        ) as pilot:
            card = pilot.app.screen.query_one("#managed-card")
            assert card.has_focus

    async def test_first_existing_focused_when_managed_not_recommended(self):
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            fake_caps(has_ssh_keygen=True),
        ) as pilot:
            first = pilot.app.screen.query_one("#ssh-card-0")
            assert first.has_focus

    async def test_recommended_pill_only_on_managed_when_recommended(self):
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            caps_managed_recommended(),
        ) as pilot:
            assert pilot.app.screen.query("#recommended-pill")
            assert "recommended" in str(
                pilot.app.screen.query_one("#recommended-pill").renderable
            )

    async def test_no_recommended_pill_when_not_recommended(self):
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            fake_caps(has_ssh_keygen=True),
        ) as pilot:
            assert not pilot.app.screen.query("#recommended-pill")

    async def test_no_separate_action_buttons(self):
        # Plan: "No separate buttons. Each card IS the action."
        from textual.widgets import Button
        async with mounted(KeyPickScreen, gs_key_pick(ssh_keys=(ssh(),))) as pilot:
            standalone = [
                b for b in pilot.app.screen.query(Button)
                if b.id and not (b.id.startswith("ssh-card-") or b.id.startswith("gpg-card-") or b.id == "managed-card")
            ]
            assert not standalone

    async def test_no_paragraph_explaining_recommended(self):
        # Plan: "small muted recommended pill — no paragraph of explanation".
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            caps_managed_recommended(),
        ) as pilot:
            assert not has_text(pilot, "We recommend")
            assert not has_text(pilot, "Why is this recommended")

    async def test_gpg_keys_without_usable_email_are_hidden(self):
        # Plan Q&A: "Existing GPG keys without usable email are not shown".
        # An ExistingKey models a GPG key as `label=email`. A blank or
        # non-usable label means the key must not appear as a card.
        usable = ExistingKey(fingerprint="AAAA0001", label="alice@example.com")
        no_email = ExistingKey(fingerprint="BBBB0002", label="")
        async with mounted(
            KeyPickScreen,
            gs_key_pick(gpg_keys=(usable, no_email)),
            fake_caps(has_gpg=True),
        ) as pilot:
            cards = pilot.app.screen.query(".key-card")
            gpg_cards = [c for c in cards if c.id and c.id.startswith("gpg-card-")]
            assert len(gpg_cards) == 1

    async def test_focused_card_has_preview(self):
        # Plan: "Faint single-line preview ... when focused; other cards
        # show no preview." Encoded as a `.key-preview` child on the focused
        # card only.
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            fake_caps(has_ssh_keygen=True),
        ) as pilot:
            focused = pilot.app.screen.query_one("#ssh-card-0")
            previews = focused.query(".key-preview")
            assert len(previews) >= 1

    async def test_unfocused_cards_have_no_preview(self):
        async with mounted(
            KeyPickScreen,
            gs_key_pick(ssh_keys=(ssh(),)),
            fake_caps(has_ssh_keygen=True),
        ) as pilot:
            managed = pilot.app.screen.query_one("#managed-card")
            assert not managed.query(".key-preview")
