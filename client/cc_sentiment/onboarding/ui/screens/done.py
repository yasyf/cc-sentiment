from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class DoneScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.DONE)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "All set",
            "verification_card_title": "Verification",
            "payload_card_title": "What gets sent",
            "payload_exclusion": "No transcript text, prompts, tool inputs, tool outputs, or code.",
            "primary_button": "Start processing",
            "verification_ssh_github": "Verification: @{cid} on GitHub",
            "verification_gist": "Verification: @{cid} via public gist",
            "verification_gpg_github": "Verification: @{cid} on GitHub",
            "verification_gpg_fpr": "Verification: GPG {fpr_short}",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Success screen — the user is verified and ready to ingest. Two
        short cards: who/how we know it's you, and exactly what we'll
        upload. One obvious primary action. Reuses the existing
        DoneBranch (per plan: "Done — Existing DoneBranch").

        Path-dependent rendering — read inline:
          - The contributor handle comes from `gs.identity.github_username`.
          - The verification line is picked from `gs.selected.source`:
              EXISTING_SSH  → VERIFICATION_SSH_GITHUB (via @cid on GitHub)
              EXISTING_GPG  → VERIFICATION_GPG_FPR (with fpr_short)
                              when the GPG branch went through email,
                              else VERIFICATION_GPG_GITHUB.
              MANAGED+ssh   → VERIFICATION_GIST (managed gist) or
                              VERIFICATION_SSH_GITHUB (managed via gh-add)
                              based on which path resolved.
              MANAGED+gpg   → VERIFICATION_GPG_FPR.
          - The fpr_short value is `gs.selected.key.fingerprint[-8:]`
            when applicable.

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

               [ Start processing ]                    (was SETTINGS_PRIMARY_LABEL)

        Buttons (exactly — matches existing screen):
          - Primary "Start processing" — dismisses the setup dialog with
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
