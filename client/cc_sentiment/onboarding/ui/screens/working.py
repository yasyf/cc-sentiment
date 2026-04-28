from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.pending_status import PendingSpinner


@dataclass(frozen=True)
class State(BaseState):
    pass


class WorkingView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    WorkingView > Card { min-width: 50; max-width: 60; }
    WorkingView Horizontal { width: auto; height: auto; }
    WorkingView Horizontal > PendingSpinner { margin: 0 1 0 0; }
    WorkingView Static#status {
        width: auto;
        color: $text-muted;
    }
    """

    def __init__(self, *, title: str, status: str) -> None:
        super().__init__()
        self.title = title
        self._initial_status = status

    def compose_card(self) -> ComposeResult:
        with Horizontal():
            yield PendingSpinner()
            yield Static(self._initial_status, id="status")


class WorkingScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.WORKING)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Setting up…",
            "status_creating_key": "Creating your signature…",
            "status_creating_gist": "Creating GitHub gist…",
            "status_verifying": "Verifying…",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Spare working screen for the managed-SSH happy path. One spinner,
        one status line that updates as work progresses. Nothing else
        (per plan: "Spinner-only auto path. ... No buttons, no checklist").

        Layout (centered card, ~50 columns):
          ╭─ Setting up… ──────────────────────╮       (WORKING_TITLE)
          │                                    │
          │  ⠹ Creating your signature…        │       (status line, cycles)
          │                                    │
          ╰────────────────────────────────────╯

        Status line cycles through (existing strings from working.py):
          "Creating your signature…"
          "Creating GitHub gist…"
          "Verifying…"

        Buttons (exactly):
          NONE. The screen has no buttons, no checklist of substeps, no
          progress bar, no elapsed timer, no cancel. Transitions to Done
          on success or Trouble after retries are exhausted.

        Subtle hints:
          - The card title doesn't change.
          - The spinner is the only animated element.
          - On a transient managed-keygen failure (per plan Q&A: "3
            quick silent retries, then Trouble"), the line stays the
            same and the spinner keeps spinning. The user never sees
            the retry.
        """
        s = self.strings()
        return WorkingView(title=s["title"], status=s["status_creating_key"])
