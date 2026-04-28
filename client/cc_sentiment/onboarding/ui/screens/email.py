from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class EmailScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.EMAIL)

    def render(self) -> t.Screen:
        """
        Email entry form for the OpenPGP path. Pre-fill if we already
        know a usable email (from gh, recent commits, or the picked GPG
        key); otherwise blank.

        Layout (centered card, ~60 columns):
          ╭─ Where should we send the link? ───╮
          │                                    │
          │  We'll send a one-time             │
          │  verification link. Open it,       │
          │  click, and you're done.           │
          │                                    │
          │  [ yasyf@example.com_____________ ]│
          │                                    │
          │       [ Send link ]                │
          ╰────────────────────────────────────╯

        Actions:
          - Input field — focused on mount, pre-filled when we have a
            confident email address.
          - Primary "Send link" — uploads the public key to
            keys.openpgp.org and requests verification for this email.

        State variants:
          - Sending: button disabled, label "Sending…", tiny spinner alongside.
          - Network error: inline message under the input —
            "Couldn't reach keys.openpgp.org. Try again in a moment." —
            button re-enables.
          - Empty: inline message — "Use an email address you can open now."

        Subtle hints:
          - One field, one button. No "use a different email server" toggle,
            no PGP-curious explainer.
          - The words "GPG" / "OpenPGP" never appear in user-facing copy
            on this screen.
        """
        ...
