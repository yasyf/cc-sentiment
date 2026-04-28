from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import Event, KeyPicked
from cc_sentiment.onboarding.state import ExistingKey
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import (
    GpgKeyCard,
    KeyCard,
    ManagedKeyCard,
    SshKeyCard,
)
from cc_sentiment.tui.widgets.card_screen import CardScreen


@dataclass(frozen=True)
class State(BaseState):
    pass


class KeyPickView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    KeyPickView > Card { min-width: 60; max-width: 80; }
    """

    def __init__(
        self,
        *,
        title: str,
        ssh_keys: tuple[ExistingKey, ...],
        gpg_keys: tuple[ExistingKey, ...],
        managed_recommended: bool,
        managed_label: str,
        managed_subline: str,
        recommended_pill: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.ssh_keys = ssh_keys
        self.gpg_keys = gpg_keys
        self.managed_recommended = managed_recommended
        self.managed_label = managed_label
        self.managed_subline = managed_subline
        self.recommended_pill = recommended_pill

    def compose_card(self) -> ComposeResult:
        managed_focused = self.managed_recommended
        first_focused = (not managed_focused) and bool(self.ssh_keys or self.gpg_keys)

        for index, key in enumerate(self.ssh_keys):
            yield SshKeyCard(
                key,
                index=index,
                focused=first_focused and index == 0,
            )
        for index, key in enumerate(self.gpg_keys):
            yield GpgKeyCard(
                key,
                index=index,
                focused=first_focused and not self.ssh_keys and index == 0,
            )
        yield ManagedKeyCard(
            recommended=self.managed_recommended,
            focused=managed_focused,
            label=self.managed_label,
            subline=self.managed_subline,
            recommended_label=self.recommended_pill,
        )

    @on(KeyCard.Selected)
    def _picked(self, event: KeyCard.Selected) -> None:
        self.dismiss(KeyPicked(source=event.source, key=event.key))


class KeyPickScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.KEY_PICK)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Pick your signature",
            "managed_card_label": "Create a new signature for cc-sentiment",
            "managed_card_subline": "Dedicated to cc-sentiment, stored under ~/.cc-sentiment/keys.",
            "recommended_pill": "recommended",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Card-based picker for which key to use. Big polished TUI cards/rows
        — never a table (per plan: "Existing-key UI must be large,
        readable, card-based, and not table-like").

        Layout (vertical stack of cards, ~70 columns):
          ╭─ Pick your signature ─────────────────╮       [DRAFT title]
          ╭───────────────────────────────────────╮
          │  ●  ~/.ssh/id_ed25519                 │
          │     ssh-ed25519 · "yasyf@host"        │
          │     ssh-ed25519 AAAA…(faint preview)  │
          ╰───────────────────────────────────────╯
          ╭───────────────────────────────────────╮
          │  ○  GPG key C7AB…D9EF                 │
          │     yasyf@example.com                 │
          ╰───────────────────────────────────────╯
          ╭───────────────────────────────────────╮
          │  ○  Create a new signature for        │       [DRAFT managed label]
          │     cc-sentiment                      │
          │     Dedicated to cc-sentiment,        │       [DRAFT managed sub-line]
          │     stored under ~/.cc-sentiment/keys │
          ╰───────────────────────────────────────╯

        Path-dependent rendering — read inline:
          - SSH cards iterate `gs.existing_keys.ssh`.
          - GPG cards iterate `gs.existing_keys.gpg`.
          - The managed card is always present.
          - Whether the managed card is "recommended" (and focused by
            default) is computed inline:
                managed_recommended = caps.gh_authenticated
            Otherwise the first existing key is focused.

        Each card (per plan "label + path/fingerprint, faint focused preview"):
          - Radio glyph (●/○) marks current focus.
          - Bold label line: path (SSH) or fingerprint (GPG).
          - Subtitle: algorithm + comment (SSH) or email (GPG).
          - Faint single-line preview of the public key body when focused;
            other cards show no preview.

        Buttons (exactly):
          - No separate buttons. Each card IS the action.
          - ↑/↓ moves focus between cards.
          - Enter / click on a card commits and advances:
              existing SSH → SshMethod
              existing GPG → Email
              managed      → Working / Publish / Email per capabilities

        Subtle hints:
          - Recommended card has a small muted "recommended" pill — no
            paragraph of explanation.
          - GPG keys with no usable email never appear here at all
            (per plan Q&A: "omit GPG keys without usable email").
        """
        s = self.strings()
        usable_gpg = tuple(k for k in gs.existing_keys.gpg if k.label)
        return KeyPickView(
            title=s["title"],
            ssh_keys=gs.existing_keys.ssh,
            gpg_keys=usable_gpg,
            managed_recommended=caps.gh_authenticated,
            managed_label=s["managed_card_label"],
            managed_subline=s["managed_card_subline"],
            recommended_pill=s["recommended_pill"],
        )
