from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class InboxScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.INBOX)

    def render(self) -> t.Screen:
        """
        Waiting card shown after the verification email has been sent.
        Replaces the email form so the user knows the request went out
        and now just needs to act on the email.

        Layout (centered card, ~60 columns):
          ╭─ Check your inbox ─────────────────╮
          │                                    │
          │  We sent a link to                 │
          │    yasyf@example.com               │
          │  Open it and click "Confirm".      │
          │                                    │
          │  ⠹ Waiting for verification…       │
          ╰────────────────────────────────────╯

        Actions:
          None primary. The screen passively polls keys.openpgp.org and
          sentiments.cc until verification succeeds (→ Done) or the
          propagation window expires (→ Trouble).

        Optional secondary actions, shown only after a polite delay (~60s):
          - Faint "Send to a different email" link → routes back to Email.
          - Faint "Already verified? Re-check now" → forces a poll.
          Neither is primary; the Inbox screen does NOT encourage "resend"
          by default.

        Subtle hints:
          - Spinner is the only animation.
          - Status line can rotate phrasing every ~10s to feel alive
            without spamming: "Waiting for verification…" → "Still
            waiting…" → "These sometimes take a moment…"
          - No explicit progress bar; no "X seconds elapsed".
          - On a transient gist-network rate-limit during background
            polling, a tiny muted note appears: "Service busy — retrying
            soon." Polling continues.
        """
        ...
