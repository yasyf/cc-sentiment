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

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Setting up…",
            "status_creating_key": "Creating cc-sentiment key…",
            "status_creating_gist": "Creating GitHub gist…",
            "status_verifying": "Verifying upload…",
        }

    def render(self) -> t.Screen:
        """
        Spare working screen for the managed-SSH happy path. One spinner,
        one status line that updates as work progresses. Nothing else
        (per plan: "Spinner-only auto path. ... No buttons, no checklist").

        Layout (centered card, ~50 columns):
          ╭─ Setting up… ──────────────────────╮       (WORKING_TITLE)
          │                                    │
          │  ⠹ Creating cc-sentiment key…      │       (status line, cycles)
          │                                    │
          ╰────────────────────────────────────╯

        Status line cycles through (existing strings from working.py):
          "Creating cc-sentiment key…"
          "Creating GitHub gist…"
          "Verifying upload…"

        Buttons (exactly):
          NONE. The screen has no buttons, no checklist of substeps, no
          progress bar, no elapsed timer, no cancel. Transitions to Done
          on success or Trouble after retries are exhausted.

        Subtle hints:
          - The card title doesn't change.
          - The spinner is the only animated element.
          - On a transient managed-keygen failure (per plan Q&A: "3
            quick silent retries, then Trouble"), the line stays the
            same and the spinner keeps spinning. The user never sees
            the retry.
        """
        ...
