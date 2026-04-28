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
        Card-based picker for which key to use. Replaces the old table —
        each option is a roomy, scannable card with the essentials (label,
        path or fingerprint) and a faint preview when focused.

        Layout (vertical stack of cards, ~70 columns):
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
          │  ○  Create a new key for cc-sentiment │
          │     Stored under ~/.cc-sentiment/keys │
          ╰───────────────────────────────────────╯

        Each card:
          - Radio glyph (●/○) for current focus.
          - Bold label line: path (SSH) or fingerprint (GPG).
          - Subtitle: algorithm + comment (SSH) or email (GPG).
          - Faint single-line preview of the public key when focused;
            other cards show no preview.

        Default focus:
          - If the managed-key option is recommended (based on the
            running router's main path), the managed card is focused.
          - Otherwise the first existing key is focused.

        Actions:
          - ↑/↓ moves focus between cards.
          - Enter / click commits the choice and advances:
              existing SSH → SshMethod
              existing GPG → Email
              managed      → Working / Publish / Email per capabilities

        Bottom row (one muted line):
          "We'll only use this to verify your uploads."

        Subtle hints:
          - Recommended card has a small muted "recommended" pill — no
            long explanation.
          - GPG keys with no usable email never appear here at all.
        """
        ...
