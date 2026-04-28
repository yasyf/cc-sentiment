from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

from rich.syntax import Syntax
from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Static

from cc_sentiment.models import (
    ClientConfig,
    GistConfig,
    GistGPGConfig,
    GPGConfig,
    SSHConfig,
)
from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import Event, StartProcessing
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


class DoneView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    DoneView > Card { min-width: 60; max-width: 80; }
    DoneView Card.done-card { margin: 0 0 1 0; }
    DoneView Static#verification-line { width: 100%; color: $success; }
    DoneView Static#payload-sample { width: 100%; }
    DoneView MutedLine#payload-exclusion { text-align: left; margin: 1 0 0 0; }
    DoneView Center > Button#start-btn { width: auto; margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title: str,
        verification_card_title: str,
        payload_card_title: str,
        payload_exclusion: str,
        primary_button: str,
        verification_line: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.verification_card_title = verification_card_title
        self.verification_line = verification_line
        self.payload_card_title = payload_card_title
        self.payload_exclusion = payload_exclusion
        self.primary_button = primary_button

    def compose_card(self) -> ComposeResult:
        yield Card(
            Static(self.verification_line, id="verification-line", classes="success"),
            title=self.verification_card_title,
            id="verification-card",
            classes="done-card",
        )
        yield Card(
            Static(
                Syntax(PAYLOAD_SAMPLE, "json", theme="github-dark", background_color=None),
                id="payload-sample",
            ),
            MutedLine(self.payload_exclusion, id="payload-exclusion"),
            title=self.payload_card_title,
            id="payload-card",
            classes="done-card",
        )
        yield Center(Button(self.primary_button, id="start-btn", variant="primary"))

    @on(Button.Pressed, "#start-btn")
    def _start(self) -> None:
        self.dismiss(StartProcessing())


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
        }

    @classmethod
    def verification_line(cls, config: ClientConfig | None) -> str:
        match config:
            case SSHConfig(contributor_id=cid):
                return f"@{cid} on GitHub"
            case GistConfig(contributor_id=cid):
                return f"@{cid} via public gist"
            case GistGPGConfig(contributor_id=cid):
                return f"@{cid} via public gist"
            case GPGConfig(contributor_type="github", contributor_id=cid):
                return f"@{cid} on GitHub"
            case GPGConfig(contributor_type="gpg", fpr=fpr):
                return f"GPG {fpr[-8:]}"
            case _:
                return "Ready"

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        return DoneView(
            **self.strings(),
            verification_line=self.verification_line(gs.verified_config),
        )
