from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class KeyPickScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.KEY_PICK)

    def render(self) -> t.Screen:
        """
        Card-based picker for which key to use. Big polished TUI cards/rows
        — never a table (per plan: "Existing-key UI must be large,
        readable, card-based, and not table-like").

        Layout (vertical stack of cards, ~70 columns):
          ╭─ Pick your verification key ──────────╮       [DRAFT title]
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
          │  ○  Create a new key for cc-sentiment │       [DRAFT managed label]
          │     A dedicated key, stored under     │       [DRAFT managed sub-line]
          │     ~/.cc-sentiment/keys              │
          ╰───────────────────────────────────────╯

        Each card (per plan "label + path/fingerprint, faint focused preview"):
          - Radio glyph (●/○) marks current focus.
          - Bold label line: path (SSH) or fingerprint (GPG).
          - Subtitle: algorithm + comment (SSH) or email (GPG).
          - Faint single-line preview of the public key body when focused;
            other cards show no preview.

        Default focus (per plan Q&A "Key choice default"):
          - If the managed key is recommended (router suggests it), the
            managed card is focused.
          - Otherwise the first existing key is focused.

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
        ...
