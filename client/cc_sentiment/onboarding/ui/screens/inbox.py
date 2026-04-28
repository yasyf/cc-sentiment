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

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Check your inbox",
            "body": (
                "Verification email sent to {email}. "
                "Open it, click the link, then return here."
            ),
            "waiting_label": "Waiting for verification…",
            "still_waiting_label": "Still waiting…",
            "taking_a_moment_label": "These sometimes take a moment…",
            "different_email_link": "Send to a different email →",
            "recheck_link": "Check again",
            "rate_limit_note": "Service busy — retrying soon.",
        }

    def render(self) -> t.Screen:
        """
        Waiting card shown after the verification email has been sent.
        Replaces the email form so the user knows the request went out
        and now just needs to act on the email.

        Layout (centered card, ~60 columns):
          ╭─ Check your inbox ─────────────────╮       [DRAFT title]
          │                                    │
          │  Verification email sent to        │       (OPENPGP_AFTER_SEND,
          │    yasyf@example.com               │        adapted: same wording,
          │  Open it, click the link, then     │        rendered across lines)
          │  return here.                      │
          │                                    │
          │  ⠹ Waiting for verification…       │       [DRAFT polling status]
          ╰────────────────────────────────────╯

        Buttons (exactly — per plan "no Reopen verification or
        resend-primary behavior"):
          - NONE primary. The screen passively polls keys.openpgp.org
            and sentiments.cc until verification succeeds (→ Done) or
            the propagation window expires (→ Trouble).

          - After a polite delay (~60s), two quiet secondary links
            appear, neither primary, both muted:
              · "Send to a different email →"  → routes back to Email.
              · "Re-check now"                  → forces a poll.

        Subtle hints:
          - The spinner is the only animation up to the delay.
          - The polling status line can rotate phrasing every ~10s to
            feel alive without spamming:
              "Waiting for verification…"
              → "Still waiting…"
              → "These sometimes take a moment…"
          - No explicit progress bar; no "X seconds elapsed".
          - On a transient rate-limit during polling, a tiny muted note
            appears: "Service busy — retrying soon." Polling continues.
        """
        ...
