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
        upload. One obvious primary action. Reuses the existing
        DoneBranch (per plan: "Done — Existing DoneBranch").

        Layout (stacked cards, ~70 columns):
          ╭─ Verification ─────────────────────╮       (existing card title)
          │  Verification: @yasyf on GitHub    │       (derived from config)
          ╰────────────────────────────────────╯
          ╭─ What gets sent ───────────────────╮       (existing card title)
          │  {                                 │       (PAYLOAD_SAMPLE, syntax-highlighted)
          │    "time": "2026-04-15T14:23:05Z", │
          │    "sentiment_score": 4,           │
          │    "claude_model":                 │
          │       "claude-haiku-4-5",          │
          │    "turn_count": 14,               │
          │    "tool_calls_per_turn": 3.2,     │
          │    "read_edit_ratio": 0.71         │
          │  }                                 │
          │                                    │
          │  No transcript text, prompts,      │       (PAYLOAD_EXCLUSION_TEXT)
          │  tool inputs, tool outputs, or     │
          │  code.                             │
          ╰────────────────────────────────────╯

               [ Start ingesting ]                     (SETTINGS_PRIMARY_LABEL)

        Verification line varies by config (existing _derive_verification):
          SSHConfig:      "Verification: @{cid} on GitHub"
          GistConfig:     "Verification: @{cid} via public gist"
          GistGPGConfig:  "Verification: @{cid} via public gist"
          GPGConfig (github): "Verification: @{cid} on GitHub"
          GPGConfig (gpg):    "Verification: GPG {fpr[-8:]}"

        Buttons (exactly — matches existing screen):
          - Primary "Start ingesting" — dismisses the setup dialog with
            a success result so the host app can begin scan/upload.
          - No other actions.

        Subtle hints:
          - JSON sample renders with light syntax highlighting on a
            transparent background.
          - The exclusion line is quiet but unmissable — users have asked
            specifically for this assurance.
          - No "advanced settings" link, no "edit" affordances.
        """
        ...
