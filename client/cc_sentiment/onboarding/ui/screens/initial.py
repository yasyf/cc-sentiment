from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.pending_status import PendingSpinner


@dataclass(frozen=True)
class State(BaseState):
    pass


class InitialView(t.Screen[None]):
    DEFAULT_CSS: ClassVar[str] = """
    InitialView { align: center middle; }
    InitialView > Horizontal { width: auto; height: auto; }
    InitialView > Horizontal > PendingSpinner { margin: 0 1 0 0; }
    InitialView > Horizontal > Static#status {
        width: auto;
        color: $text-muted;
    }
    """

    STILL_CHECKING_AFTER_SECONDS: ClassVar[float] = 2.0

    def __init__(self, *, checking: str, still_checking: str) -> None:
        super().__init__()
        self.checking = checking
        self.still_checking = still_checking

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield PendingSpinner()
            yield Static(self.checking, id="status")

    def on_mount(self) -> None:
        self.set_timer(self.STILL_CHECKING_AFTER_SECONDS, self._mark_still_checking)

    def _mark_still_checking(self) -> None:
        self.query_one("#status", Static).update(self.still_checking)


class InitialScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.INITIAL)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "checking": "Checking your setup…",
            "still_checking": "Still checking…",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Spare loading screen during the initial probe — usually too quick
        to register, but graceful when the network is slow.

        Layout:
          Centered, no border:
            ⠹ Checking your setup…           (reuses WELCOME_CHECKING)

        Behavior:
          No buttons, no actions. Spinner glyph cycles. Transitions out
          as soon as the probe resolves to one of: Done, SavedRetry,
          Welcome, Publish, or Inbox.

        Subtle hints:
          The spinner is the only motion. If the probe takes >2s, the
          line can update once to "Still checking…" — calm, never
          alarming.
        """
        return InitialView(**self.strings())
