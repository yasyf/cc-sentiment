from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class SshMethodScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.SSH_METHOD)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Where should we publish this key?",
            "username_label": "GitHub username",
            "username_placeholder": "yasyf",
            "gist_button": "Publish as a gist",
            "gist_subline": "Public gist on github.com/{username}. Delete it any time.",
            "gh_add_link": "Add it to GitHub →",
            "gh_add_subline_authed": "We'll add it via the GitHub CLI.",
            "gh_add_subline_manual": "You'll paste it into github.com/settings/keys.",
        }

    def render(self) -> t.Screen:
        """
        Dedicated method picker after the user has picked an existing SSH
        key. Two methods, gist is the default. May also show an inline
        username input when the user picked an existing SSH key but we
        still don't know their GitHub username (per plan: "If username
        is missing, ask inline on this method screen").

        Layout (centered card, ~60 columns; username row appears only
        when missing):
          ╭─ Where should we publish this key? ─╮      [DRAFT title]
          │                                     │
          │  GitHub username                    │      (only when missing)
          │  [ yasyf____________________ ]      │
          │                                     │
          │       [ Publish as a gist ]         │      [DRAFT primary label]
          │       Public gist on github.com/    │      [DRAFT sub-line]
          │       <username>. Delete it any time│
          │                                     │
          │       Add it to GitHub →            │      [DRAFT secondary label]
          │       (gh authed)                   │      [DRAFT sub-line variants]
          │       We'll add it via the GitHub   │
          │       CLI.
          │       (no gh)
          │       You'll paste it into          │
          │       github.com/settings/keys.     │
          ╰─────────────────────────────────────╯

        Buttons (exactly):
          - Optional username input (only when missing).
          - Primary "Publish as a gist" — focused. Routes to Publish.
          - Secondary "Add it to GitHub →" — routes to GhAdd.
          - No third option, no comparison table, no help link.

        De-emphasis when not gh-authed (per plan Q&A "GitHub add for
        existing SSH"):
          The "Add it to GitHub →" link uses a more muted color and the
          sub-line clearly states it will be manual. Never red, never
          alarming, just clearly the second-best path.

        Subtle hints:
          - Tab/Shift-Tab moves between the two options; Enter activates.
          - Plan: "default gist; explain tradeoffs" — sub-lines under
            each option carry the tradeoff in one line each.
        """
        ...
