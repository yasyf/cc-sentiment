from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class WelcomeScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WELCOME)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Set up cc-sentiment",
            "body": (
                "We'll create your signature so we can confirm uploads "
                "are yours. This usually takes about 30 seconds."
            ),
            "primary_button": "Get started",
            "checking": "Checking your setup…",
            "saved_invalid_line": "Your last verification needs refreshing.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Friendly entry point for first-time setup. Calm prose, one obvious
        action, and a subtle hint that something is happening in the
        background.

        Layout (single centered card, ~60 columns):
          ╭─ Set up cc-sentiment ──────────────╮       (WELCOME_TITLE)
          │                                    │
          │  We'll create your signature so   │       (WELCOME_BODY)
          │  so we can confirm uploads are     │
          │  yours. This usually takes about   │
          │  30 seconds.                       │
          │                                    │
          │       [ Get started ]              │       (WELCOME_CTA)
          │                                    │
          │  ⠹ Checking your setup…            │       (WELCOME_CHECKING)
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Primary "Get started" — always visible (per plan: "Welcome
            always shows Get started; checking status is separate and
            subtle"). Kicks off discovery.
          - No other actions. The "I don't use GitHub →" link does NOT
            appear here — it lives only on UserForm and Publish.

        Path-dependent rendering — read inline:
          When `gs.has_saved_config` is True (we got bumped here from a
          saved-invalid probe), one extra muted line appears ABOVE the
          body: "Your last verification needs refreshing." No accusations,
          no error tone, no debug.

        Subtle hints:
          - "Checking your setup…" is a faint spinner row below the
            button; it disappears quietly when discovery resolves.
          - Discovery is idempotent — clicking Get started multiple
            times never restarts it (per plan).
          - If discovery finishes before the user clicks, the button
            label can shift to "Continue" so the action stays obvious.
          - No tables of detected tools, no debug rows.
        """
        ...
