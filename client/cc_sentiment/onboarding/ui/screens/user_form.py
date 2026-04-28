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
        Username form, shown only as a last resort when ssh-keygen exists
        but we still don't know who the user is on GitHub. One clear
        question, one input, one obvious primary, one quiet escape hatch.

        Layout (centered card, ~60 columns):
          ╭─ What's your GitHub username? ─────╮       (from plan, exact)
          │                                    │
          │  [ yasyf______________________ ]   │       (USERNAME_PLACEHOLDER)
          │                                    │
          │       [ Continue ]                 │       (existing button label)
          │                                    │
          │  I don't use GitHub →              │       (USERNAME_NO_GITHUB_LINK)
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Input — focused on mount; placeholder shows the example
            username "yasyf" in muted text.
          - Primary "Continue" — validates against the GitHub API and
            routes per capabilities (Working when gh authed, Publish
            otherwise). While in flight, the button shows a tiny spinner
            and disables.
          - Quiet "I don't use GitHub →" — opts out (sets
            github_lookup_allowed=False) and routes to Email if GPG is
            available, otherwise Blocked.

        State variants (inline messages below the input):
          - Empty submit:    USERNAME_ERROR_EMPTY
              "Enter your GitHub username, or pick "I don't use GitHub" below."
          - 404:             USERNAME_ERROR_NOT_FOUND ("GitHub user "{user}"
              wasn't found.") — input refocuses.
          - Network down:    USERNAME_ERROR_UNREACHABLE
              "Couldn't reach GitHub. Try again in a moment."
              Button stays active so the user just presses Continue
              again (per plan: "Username validation network — Retry
              in place").
          - Validating:      faint "Validating yasyf…" beside the
                             disabled button.

        Subtle hints:
          - No body paragraph above the input — the title IS the question.
          - No tables, no progress bars, no debug.
          - "I don't use GitHub →" is muted; only colored on hover.
        """
        ...
