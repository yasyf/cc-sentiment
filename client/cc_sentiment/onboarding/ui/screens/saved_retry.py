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

    def render(self) -> t.Screen:
        """
        Small recovery card for when saved credentials exist but
        sentiments.cc is currently unreachable. Reassuring — implies the
        setup itself is fine, the network just hiccuped.

        Layout (small centered card, ~50 columns):
          ╭─ Couldn't reach sentiments.cc ──╮
          │  Your network might be slow or  │
          │  offline. Try again in a moment.│
          │                                 │
          │       [ Retry ]                 │
          │       Set up again →            │
          ╰─────────────────────────────────╯

        Actions:
          - Primary "Retry" (focused) — re-probes saved config. Stays here
            if still unreachable, transitions to Done on success, or to
            Welcome if the server now reports the saved config invalid.
          - Quiet "Set up again →" — drops the saved config and routes to
            Welcome.

        Subtle hints:
          - No technical auth dump, no error codes.
          - During an in-flight retry, "Retry" disables and a tiny spinner
            sits beside it. No alarming language.
        """
        ...
