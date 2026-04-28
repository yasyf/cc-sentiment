from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class WelcomeScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WELCOME)

    def render(self) -> t.Screen:
        """
        The friendly entry point — what a first-time user sees. Calm prose,
        one obvious action, a subtle hint that something is happening in
        the background.

        Layout (single centered card, ~60 columns):
          ╭─ Set up cc-sentiment ──────────────╮
          │                                    │
          │  We'll create a verification key   │
          │  so we can confirm uploads are     │
          │  yours. Takes about 30 seconds.    │
          │                                    │
          │       [ Get started ]              │
          │                                    │
          │  ⠹ Checking your setup…            │
          ╰────────────────────────────────────╯

        Actions:
          - Primary "Get started" (focused) — kicks off discovery and lets
            the dispatcher route us forward.
          - No secondary actions on this screen. No "skip", no settings.

        State variants:
          - Default copy as above.
          - GlobalState.has_saved_config=True (came from "saved invalid"):
            one extra gentle line above the body — "Your last setup needs
            refreshing." No accusations, no "your key was rejected" tone.

        Subtle hints:
          - The "Checking your setup…" row is faint, sits below the button,
            and quietly disappears when discovery resolves.
          - If discovery finishes before the user clicks Get started, the
            button label can shift to "Continue" so the action stays obvious.
          - No tables of detected tools, no debug rows.
        """
        ...
