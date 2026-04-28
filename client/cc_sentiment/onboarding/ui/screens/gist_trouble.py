from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState, TroubleReason
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class GistTroubleScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(
            stage=Stage.TROUBLE,
            trouble_reason=TroubleReason.GIST_TIMEOUT,
        )

    def render(self) -> t.Screen:
        """
        Trouble screen for when we've been watching for a gist that never
        appears. Most common cause is a typo in the username, so we put
        the username edit inline and offer email as the next-best path.

        Layout (card, ~60 columns):
          ╭─ Still watching for your gist ─────╮
          │                                    │
          │  GitHub usually takes a moment,    │
          │  but if you typed the username     │
          │  wrong we'll never find it.        │
          │                                    │
          │  GitHub username                   │
          │  [ yasyf____________________ ]     │
          │  [ Try this username ]             │
          │                                    │
          │  Use email instead →               │
          │  Restart setup →                   │
          ╰────────────────────────────────────╯

        Actions:
          - Inline username input pre-filled with the username we've been
            polling against; user edits, presses "Try this username" to
            validate and route back to Publish with the new value.
          - Quiet "Use email instead →" — routes to Email (only shown
            when GPG is available).
          - Quiet "Restart setup →" — clears pending state and routes
            to Welcome.

        Subtle hints:
          - The original watcher is still running in the background — if
            the gist appears while the user is reading this screen, we
            advance to Done without further interaction.
          - No retry counter, no scary error text. The "still watching"
            framing keeps it calm.
          - Server-side error, when present, appears as a single muted
            line below the input — never red, never large.
        """
        ...
