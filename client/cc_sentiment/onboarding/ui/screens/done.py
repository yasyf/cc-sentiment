from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.state import KeySource, SelectedKey
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.card import Card
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.muted_line import MutedLine


PAYLOAD_SAMPLE: str = json.dumps(
    {
        "time": "2026-04-15T14:23:05Z",
        "sentiment_score": 4,
        "claude_model": "claude-haiku-4-5",
        "turn_count": 14,
        "tool_calls_per_turn": 3.2,
        "read_edit_ratio": 0.71,
    },
    indent=2,
)


@dataclass(frozen=True)
class State(BaseState):
    pass


class DoneView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    DoneView > Card { min-width: 60; max-width: 80; }
    DoneView Card.done-card { margin: 0 0 1 0; }
    DoneView Static#verification-line { width: 100%; color: $text; }
    DoneView Static#payload-sample { width: 100%; }
    DoneView MutedLine#payload-exclusion { text-align: left; margin: 1 0 0 0; }
    DoneView Center > Button#start-btn { width: auto; margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title: str,
        verification_card_title: str,
        verification_line: str,
        payload_card_title: str,
        payload_exclusion: str,
        primary_label: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.verification_card_title = verification_card_title
        self.verification_line = verification_line
        self.payload_card_title = payload_card_title
        self.payload_exclusion = payload_exclusion
        self.primary_label = primary_label

    def compose_card(self) -> ComposeResult:
        yield Card(
            Static(self.verification_line, id="verification-line"),
            title=self.verification_card_title,
            id="verification-card",
            classes="done-card",
        )
        yield Card(
            Static(PAYLOAD_SAMPLE, id="payload-sample", markup=False),
            MutedLine(self.payload_exclusion, id="payload-exclusion"),
            title=self.payload_card_title,
            id="payload-card",
            classes="done-card",
        )
        yield Center(Button(self.primary_label, id="start-btn", variant="primary"))


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

    @classmethod
    def verification_line(cls, gs: GlobalState) -> str:
        s = cls.strings()
        selected = gs.selected
        assert isinstance(selected, SelectedKey)
        username = gs.identity.github_username
        fpr = selected.key.fingerprint if selected.key else ""
        fpr_short = fpr[-8:]

        match selected.source:
            case KeySource.EXISTING_SSH:
                return s["verification_ssh_github"].format(cid=username)
            case KeySource.EXISTING_GPG:
                if username:
                    return s["verification_gpg_github"].format(cid=username)
                return s["verification_gpg_fpr"].format(fpr_short=fpr_short)
            case KeySource.MANAGED:
                if fpr.startswith("SHA256:"):
                    return s["verification_gist"].format(cid=username)
                return s["verification_gpg_fpr"].format(fpr_short=fpr_short)

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
        s = self.strings()
        return DoneView(
            title=s["title"],
            verification_card_title=s["verification_card_title"],
            verification_line=self.verification_line(gs),
            payload_card_title=s["payload_card_title"],
            payload_exclusion=s["payload_exclusion"],
            primary_label=s["primary_button"],
        )
