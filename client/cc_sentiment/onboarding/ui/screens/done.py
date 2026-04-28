from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class DoneScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.DONE)

    def render(self) -> t.Screen:
        """
        Success screen — the user is verified and ready to ingest. Two
        short cards: who/how we know it's you, and exactly what we'll
        upload. One obvious primary action.

        Layout (stacked cards, ~70 columns):
          ╭─ Verification ─────────────────────╮
          │  ✓ Verification: @yasyf on GitHub  │
          ╰────────────────────────────────────╯
          ╭─ What gets sent ───────────────────╮
          │  {                                 │
          │    "time": "2026-04-15T14:23:05Z", │
          │    "sentiment_score": 4,           │
          │    "claude_model":                 │
          │       "claude-haiku-4-5",          │
          │    "turn_count": 14,               │
          │    "tool_calls_per_turn": 3.2,     │
          │    "read_edit_ratio": 0.71         │
          │  }                                 │
          │                                    │
          │  No transcript text, prompts,      │
          │  tool inputs, tool outputs, or     │
          │  code.                             │
          ╰────────────────────────────────────╯

               [ Start ingesting ]

        Verification line varies by config:
          SSH on GitHub:    "Verification: @yasyf on GitHub"
          Public gist:      "Verification: @yasyf via public gist"
          GPG on GitHub:    "Verification: @yasyf on GitHub"
          GPG by fingerprint: "Verification: GPG …D9EF"

        Actions:
          - Primary "Start ingesting" — dismisses the setup dialog with a
            success result so the host app can begin the scan/upload flow.

        Subtle hints:
          - The JSON sample is rendered with light syntax highlighting on
            a transparent background.
          - The "what gets sent" exclusion line is quiet but unmissable —
            users have asked specifically for this assurance.
          - No "advanced settings" link, no "edit" affordances.
        """
        ...
