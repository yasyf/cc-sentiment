from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class InitialScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.INITIAL)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "checking": "Checking your setup…",
            "still_checking": "Still checking…",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Spare loading screen during the initial probe — usually too quick
        to register, but graceful when the network is slow.

        Layout:
          Centered, no border:
            ⠹ Checking your setup…           (reuses WELCOME_CHECKING)

        Behavior:
          No buttons, no actions. Spinner glyph cycles. Transitions out
          as soon as the probe resolves to one of: Done, SavedRetry,
          Welcome, Publish, or Inbox.

        Subtle hints:
          The spinner is the only motion. If the probe takes >2s, the
          line can update once to "Still checking…" — calm, never
          alarming.
        """
        ...
