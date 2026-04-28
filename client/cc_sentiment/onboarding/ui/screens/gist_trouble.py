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
        Trouble screen for when we've watched for the gist long enough
        and never found it. Most common cause is a typo in the username,
        so we put the username edit inline and offer email as the alternate
        path. No restart link here (per plan: "Keep actions branch-specific;
        no extraneous buttons" — restart belongs to VerifyTrouble only).

        Layout (card, ~60 columns):
          ╭─ Still watching for your gist ─────╮       (TROUBLE_TITLE)
          │                                    │
          │  GitHub usually takes a moment,    │       [DRAFT body]
          │  but if the username is off we'll  │
          │  never find it.                    │
          │                                    │
          │  GitHub username                   │
          │  [ yasyf____________________ ]     │
          │  [ Try this username ]             │       [DRAFT submit label]
          │                                    │
          │  Use email instead →               │       [DRAFT email link label]
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Inline username input pre-filled with the username we've
            been polling against.
          - Small "Try this username" button — validates and routes back
            to Publish with the new username.
          - Quiet "Use email instead →" link — routes to Email (only
            shown when GPG is available).
          - NO restart link, NO "keep watching", NO "try a different
            way" (existing buttons are removed per plan).

        Subtle hints:
          - The original watcher is still running in the background — if
            the gist appears while the user is reading this screen, we
            advance to Done without further interaction.
          - No retry counter, no scary error text. The "still watching"
            framing keeps it calm.
          - On gist API rate-limit during the still-running watcher, a
            tiny muted note appears: "GitHub busy — still trying."
        """
        ...
