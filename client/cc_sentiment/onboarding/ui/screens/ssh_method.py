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

    def render(self) -> t.Screen:
        """
        Dedicated method picker for an existing SSH key. Two options;
        publishing as a public gist is the default because it doesn't
        require GitHub auth and leaves no permanent change on the user's
        account.

        Layout (centered card, ~60 columns):
          ╭─ Where should we publish this key? ─╮
          │                                     │
          │  We need somewhere public so        │
          │  sentiments.cc can verify it's      │
          │  yours.                             │
          │                                     │
          │       [ Publish as a gist ]         │
          │                                     │
          │       Add it to GitHub →            │
          ╰─────────────────────────────────────╯

        Actions:
          - Primary "Publish as a gist" — focused. Routes to Publish.
            Below it, one quiet line:
              "Public gist on github.com/<username>. Delete it any time."
          - Secondary "Add it to GitHub →" — routes to GhAdd.
            Below it, one quiet capability-aware line:
              gh authed:    "We'll add it via the GitHub CLI."
              not authed:   "You'll paste it into github.com/settings/keys."

        De-emphasis when not gh-authed:
          The "Add it to GitHub →" link uses a more muted color and the
          "manual" sub-line stays — never red, never alarming, just clearly
          the second-best path.

        Subtle hints:
          - Tab/Shift-Tab moves between the two options; Enter activates.
          - No third "advanced" option, no comparison table.
        """
        ...
