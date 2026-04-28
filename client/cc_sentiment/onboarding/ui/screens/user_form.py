from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class UserFormScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.USER_FORM)

    def render(self) -> t.Screen:
        """
        The username form, shown when ssh-keygen exists but we don't know
        who the user is on GitHub. One question, one input, an obvious
        primary action, and a quiet escape hatch.

        Layout (centered card, ~60 columns):
          ╭─ What's your GitHub username? ─────╮
          │                                    │
          │  We'll use it to find the gist     │
          │  with your verification key.       │
          │                                    │
          │  [ yasyf______________________ ]   │
          │                                    │
          │       [ Continue ]                 │
          │                                    │
          │  I don't use GitHub →              │
          ╰────────────────────────────────────╯

        Actions:
          - Input — focused on mount; placeholder shows an example username
            in muted text.
          - Primary "Continue" — validates against the GitHub API; routes
            to Working / Publish based on capabilities. While in flight,
            the button shows a tiny spinner and disables.
          - Quiet "I don't use GitHub →" — opts out and routes to Email
            (if GPG available) or Blocked otherwise.

        State variants:
          - Validating: button disabled, faint "Validating yasyf…" beside it.
          - Not found (404): inline error below the input —
            "GitHub user "yasyf" wasn't found." Input refocuses.
          - Unreachable (network): inline retry —
            "Couldn't reach GitHub. Try again in a moment." Button stays
            active so the user can simply press Continue again.

        Subtle hints:
          - No tables, no progress bars, no debug.
          - "I don't use GitHub →" link is muted; only colored on hover.
        """
        ...
