from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center, Horizontal
from textual.widgets import Button, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import DiscoveryComplete, Event
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.muted_line import MutedLine
from cc_sentiment.tui.widgets.pending_status import PendingSpinner


@dataclass(frozen=True)
class State(BaseState):
    pass


class WelcomeView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    WelcomeView > Card { min-width: 60; max-width: 70; }
    WelcomeView Center > Button#get-started-btn { width: auto; margin: 0 0 1 0; }
    WelcomeView Horizontal#checking-row {
        width: auto;
        height: auto;
        align: center middle;
    }
    WelcomeView Horizontal#checking-row > PendingSpinner { margin: 0 1 0 0; }
    WelcomeView Static#checking-status {
        width: auto;
        color: $text-muted;
    }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        primary_button: str,
        continue_button: str,
        checking: str,
        saved_invalid_line: str,
        show_saved_invalid: bool,
    ) -> None:
        super().__init__()
        self.title = title
        self.body = body
        self.primary_button = primary_button
        self.continue_button = continue_button
        self.checking = checking
        self.saved_invalid_line = saved_invalid_line
        self.show_saved_invalid = show_saved_invalid
        self._discovery: DiscoveryComplete | None = None

    def compose_card(self) -> ComposeResult:
        if self.show_saved_invalid:
            yield MutedLine(self.saved_invalid_line, id="saved-invalid-line")
        yield Body(self.body)
        yield Center(Button(self.primary_button, id="get-started-btn", variant="primary"))
        with Center():
            with Horizontal(id="checking-row"):
                yield PendingSpinner()
                yield Static(self.checking, id="checking-status")

    def discovery_done(self, event: DiscoveryComplete) -> None:
        self._discovery = event
        if event.auto_verified:
            self.dismiss(event)
            return
        self.query_one("#checking-row").display = False
        self.query_one("#get-started-btn", Button).label = self.continue_button

    @on(Button.Pressed, "#get-started-btn")
    def _click(self) -> None:
        if self._discovery is None:
            return
        self.dismiss(self._discovery)


class WelcomeScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WELCOME)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Set up cc-sentiment",
            "body": (
                "We'll create your signature so we can confirm uploads "
                "are yours. This usually takes about 30 seconds."
            ),
            "primary_button": "Get started",
            "continue_button": "Continue",
            "checking": "Checking your setup…",
            "saved_invalid_line": "Your last verification needs refreshing.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Friendly entry point for first-time setup. Calm prose, one obvious
        action, and a subtle hint that something is happening in the
        background.

        Layout (single centered card, ~60 columns):
          ╭─ Set up cc-sentiment ──────────────╮       (WELCOME_TITLE)
          │                                    │
          │  We'll create your signature so   │       (WELCOME_BODY)
          │  so we can confirm uploads are     │
          │  yours. This usually takes about   │
          │  30 seconds.                       │
          │                                    │
          │       [ Get started ]              │       (WELCOME_CTA)
          │                                    │
          │  ⠹ Checking your setup…            │       (WELCOME_CHECKING)
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Primary "Get started" — always visible (per plan: "Welcome
            always shows Get started; checking status is separate and
            subtle"). Kicks off discovery.
          - No other actions. The "I don't use GitHub →" link does NOT
            appear here — it lives only on UserForm and Publish.

        Path-dependent rendering — read inline:
          When `gs.has_saved_config` is True (we got bumped here from a
          saved-invalid probe), one extra muted line appears ABOVE the
          body: "Your last verification needs refreshing." No accusations,
          no error tone, no debug.

        Subtle hints:
          - "Checking your setup…" is a faint spinner row below the
            button; it disappears quietly when discovery resolves.
          - Discovery is idempotent — clicking Get started multiple
            times never restarts it (per plan).
          - If discovery finishes before the user clicks, the button
            label can shift to "Continue" so the action stays obvious.
          - No tables of detected tools, no debug rows.
        """
        return WelcomeView(**self.strings(), show_saved_invalid=gs.has_saved_config)
