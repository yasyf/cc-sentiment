from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState, TroubleReason
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class VerifyTroubleScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(
            stage=Stage.TROUBLE,
            trouble_reason=TroubleReason.VERIFY_TIMEOUT,
        )

    def render(self) -> t.Screen:
        """
        Trouble screen for when the key is published but sentiments.cc
        still can't verify it after the propagation window. Almost always
        a server-side problem we can't fix from here — give the user a
        clear restart action and one mapped explanation.

        Layout (card, ~60 columns):
          ╭─ We couldn't verify your key ──────╮
          │                                    │
          │  {server-code-mapped message}      │
          │                                    │
          │  Try again with a fresh setup —    │
          │  it usually only takes a moment.   │
          │                                    │
          │       [ Restart setup ]            │
          ╰────────────────────────────────────╯

        Server-code mapping (client-side, static):
          "key-not-found"    → "sentiments.cc couldn't see your published key."
          "signature-failed" → "Your key didn't match the signature we sent."
          "rate-limited"     → "sentiments.cc is busy — wait a minute and retry."
          unknown / fallback → "sentiments.cc reported an issue we don't recognize."

        Actions:
          - Primary "Restart setup" — clears pending state and routes to
            Welcome. Re-discovery picks the best path again.

        Subtle hints:
          - No raw error dump, no stack trace, no internal codes.
          - No "Keep watching" affordance — the propagation window already
            expired; more waiting won't help.
        """
        ...
