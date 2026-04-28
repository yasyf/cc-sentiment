from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class InitialScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.INITIAL)

    def render(self) -> t.Screen:
        """
        A spare loading screen shown only during the initial probe — usually
        too quick to register, but graceful when the network is slow.

        Layout:
          Centered card, no border, single line:
            ⠹ Checking your setup…

        Behavior:
          No buttons, no actions. Spinner glyph cycles. Transitions out as
          soon as the probe resolves (Done, SavedRetry, Welcome, Publish,
          or Inbox depending on saved/pending state).

        Subtle hints:
          The spinner is the only motion. If the probe takes >2s, the line
          can update to "Still checking…" — once, calm, never alarming.
        """
        ...
