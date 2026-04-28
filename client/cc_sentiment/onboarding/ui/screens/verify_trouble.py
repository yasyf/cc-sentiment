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

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "We couldn't verify your key",
            "subhint": "Try again with a fresh setup — it usually only takes a moment.",
            "restart_button": "Restart setup",
            "error_key_not_found": "sentiments.cc couldn't see your published key.",
            "error_signature_failed": "Your key didn't match the signature we sent.",
            "error_rate_limited": "sentiments.cc is busy — wait a minute and retry.",
            "error_unknown": "sentiments.cc reported an issue we don't recognize.",
        }

    def render(self) -> t.Screen:
        """
        Trouble screen for when the key is published but sentiments.cc
        still can't verify it after the propagation window. Server-side
        problem we can't fix from here — give the user a clear restart
        and one mapped explanation (per plan: "Verification timeout:
        display client-mapped server error code + restart setup").

        Layout (card, ~60 columns):
          ╭─ We couldn't verify your key ──────╮       [DRAFT title]
          │                                    │
          │  {server-code-mapped message}      │       (one of the four below)
          │                                    │
          │       [ Restart setup ]            │       [DRAFT button label]
          ╰────────────────────────────────────╯

        Server-code mapping (client-side, static — minimal core codes
        per plan Q&A "Server error-code granularity: Minimal core codes"):
          "key-not-found"    → "sentiments.cc couldn't see your published key."
          "signature-failed" → "Your key didn't match the signature we sent."
          "rate-limited"     → "sentiments.cc is busy — wait a minute and retry."
          unknown / fallback → "sentiments.cc reported an issue we don't recognize."

        Buttons (exactly):
          - Primary "Restart setup" — clears pending state and routes to
            Welcome. Re-discovery picks the best path again.
          - NO secondary actions, NO "keep watching" — the propagation
            window already expired; more waiting won't help.

        Subtle hints:
          - No raw error dump, no stack trace, no internal codes shown
            to the user.
          - No links to docs / support inside this card — restart is the
            single useful action.
        """
        ...
