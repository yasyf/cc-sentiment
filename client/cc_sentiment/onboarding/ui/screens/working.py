from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class WorkingScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WORKING)

    def render(self) -> t.Screen:
        """
        Spare working screen for the managed-SSH happy path. One spinner,
        one status line that updates as the work progresses. Nothing else.

        Layout (centered card, ~50 columns):
          ╭─ Setting up… ──────────────────────╮
          │                                    │
          │  ⠹ Creating verification key…      │
          │                                    │
          ╰────────────────────────────────────╯

        Status line cycles through:
          "Creating verification key…"
          "Creating GitHub gist…"
          "Verifying with sentiments.cc…"

        Actions:
          None. The screen has no buttons, no checklist of substeps, no
          progress bar, no elapsed timer. It transitions to Done on
          success or Trouble on failure.

        Subtle hints:
          - The card title doesn't change.
          - The spinner is the only animated element.
          - If a step takes >5s, the line can append
            "(this usually takes a few seconds)" — once, calm, then drops.
          - On a transient retry (per "Managed key creation fails? Retry
            silently"), the line stays the same and the spinner keeps
            spinning. The user never sees the retry.
        """
        ...
