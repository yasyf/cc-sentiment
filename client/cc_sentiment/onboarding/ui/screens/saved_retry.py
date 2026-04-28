from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class SavedRetryScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.SAVED_RETRY)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Couldn't reach sentiments.cc",
            "body": "We'll try again in a moment.",
            "retry_button": "Retry",
            "restart_link": "Set up again",
        }

    def render(self) -> t.Screen:
        """
        Small recovery card for when saved credentials exist but
        sentiments.cc is currently unreachable. Reassuring tone — implies
        the setup itself is fine, the network just hiccuped.

        Layout (small centered card, ~50 columns):
          ╭─ Couldn't reach sentiments.cc ──╮     [DRAFT title]
          │  We'll try again in a moment.    │     [DRAFT body]
          │                                  │
          │       [ Retry ]                  │
          │       Set up again →             │
          ╰──────────────────────────────────╯

        Buttons (exactly):
          - Primary "Retry" (focused) — re-probes the saved config.
            Stays here if still unreachable; transitions to Done on
            success, or to Welcome if the server now reports the saved
            config invalid.
          - Quiet "Set up again" → drops the saved config and routes to
            Welcome.

        Subtle hints:
          - No technical auth dump, no error codes (per plan).
          - During an in-flight retry, "Retry" disables and a tiny spinner
            sits beside it.
        """
        ...
