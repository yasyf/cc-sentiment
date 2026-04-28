from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow
from cc_sentiment.tui.widgets.title import Title


@dataclass(frozen=True)
class State(BaseState):
    pass


class SavedRetryView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    SavedRetryView > Card { min-width: 50; max-width: 60; }
    SavedRetryView Center > Button#retry-btn { width: auto; margin: 1 0 1 0; }
    """

    def __init__(self, *, title: str, body: str, retry_label: str, restart_label: str) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.retry_label = retry_label
        self.restart_label = restart_label

    def compose(self) -> ComposeResult:
        # Override CardScreen.compose to control title placement and not yield extra
        from cc_sentiment.tui.widgets.card import Card
        yield Card(
            Title(self.title),
            Body(self.body_text),
            Center(Button(self.retry_label, id="retry-btn", variant="primary")),
            LinkRow(self.restart_label, id="restart-link"),
            title="",
        )

    def compose_card(self) -> ComposeResult:
        return iter(())

    def on_mount(self) -> None:
        self.query_one("#retry-btn", Button).focus()


class SavedRetryScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.SAVED_RETRY)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Couldn't reach sentiments.cc",
            "body": "We'll try again in a moment.",
            "retry_button": "Retry",
            "restart_link": "Set up again",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Small recovery card for when saved credentials exist but
        sentiments.cc is currently unreachable. Reassuring tone — implies
        the setup itself is fine, the network just hiccuped.

        Layout (small centered card, ~50 columns):
          ╭─ Couldn't reach sentiments.cc ──╮     [DRAFT title]
          │  We'll try again in a moment.    │     [DRAFT body]
          │                                  │
          │       [ Retry ]                  │
          │       Set up again →             │
          ╰──────────────────────────────────╯

        Buttons (exactly):
          - Primary "Retry" (focused) — re-probes the saved config.
            Stays here if still unreachable; transitions to Done on
            success, or to Welcome if the server now reports the saved
            config invalid.
          - Quiet "Set up again" → drops the saved config and routes to
            Welcome.

        Subtle hints:
          - No technical auth dump, no error codes (per plan).
          - During an in-flight retry, "Retry" disables and a tiny spinner
            sits beside it.
        """
        s = self.strings()
        return SavedRetryView(
            title=s["title"],
            body=s["body"],
            retry_label=s["retry_button"],
            restart_label=s["restart_link"],
        )
