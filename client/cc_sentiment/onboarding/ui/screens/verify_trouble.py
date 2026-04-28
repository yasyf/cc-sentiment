from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button

from cc_sentiment.onboarding import (
    Capabilities,
    Stage,
    State as GlobalState,
    VerifyTimeout,
)
from cc_sentiment.onboarding.events import Event, TroubleRestart
from cc_sentiment.onboarding.state import VerifyErrorCode
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.muted_line import MutedLine


@dataclass(frozen=True)
class State(BaseState):
    pass


class VerifyTroubleView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    VerifyTroubleView > Card { min-width: 50; max-width: 60; }
    VerifyTroubleView Center > Button#restart-btn { width: auto; margin: 1 0 0 0; }
    """

    def __init__(self, *, title: str, message: str, subhint: str, restart_button: str) -> None:
        super().__init__()
        self.title = title
        self.message = message
        self.subhint = subhint
        self.restart_button = restart_button

    def compose_card(self) -> ComposeResult:
        yield Body(self.message, id="message")
        yield MutedLine(self.subhint)
        yield Center(Button(self.restart_button, id="restart-btn", variant="primary"))

    @on(Button.Pressed, "#restart-btn")
    def _restart(self) -> None:
        self.dismiss(TroubleRestart())


class VerifyTroubleScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.TROUBLE, trouble=VerifyTimeout())

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "We couldn't verify your signature",
            "subhint": "Try again with a fresh setup. It usually only takes a moment.",
            "restart_button": "Restart setup",
            "error_key_not_found": "sentiments.cc couldn't see your published signature.",
            "error_signature_failed": "Your signature wasn't accepted.",
            "error_rate_limited": "sentiments.cc is busy. Wait a minute and retry.",
            "error_unknown": "sentiments.cc reported an issue we don't recognize.",
        }

    @classmethod
    def message_for(cls, error_code: VerifyErrorCode) -> str:
        s = cls.strings()
        match error_code:
            case "key-not-found":
                return s["error_key_not_found"]
            case "signature-failed":
                return s["error_signature_failed"]
            case "rate-limited":
                return s["error_rate_limited"]
            case _:
                return s["error_unknown"]

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Trouble screen for when the key is published but sentiments.cc
        still can't verify it after the propagation window. Server-side
        problem we can't fix from here — give the user a clear restart
        and one mapped explanation (per plan: "Verification timeout:
        display client-mapped server error code + restart setup").

        Path-dependent rendering — read inline:
          - The error_code is `gs.trouble.error_code` (gs.trouble is
            guaranteed to be a VerifyTimeout when this screen renders).
          - That code maps client-side to one of the four error_* strings
            above (see Server-code mapping below).

        Layout (card, ~60 columns):
          ╭─ We couldn't verify your signature ─╮      [DRAFT title]
          │                                     │
          │  {server-code-mapped message}       │      (one of the four below)
          │                                     │
          │       [ Restart setup ]             │      [DRAFT button label]
          ╰────────────────────────────────────╯

        Server-code mapping (client-side, static — minimal core codes
        per plan Q&A "Server error-code granularity: Minimal core codes"):
          "key-not-found"    → "sentiments.cc couldn't see your published signature."
          "signature-failed" → "Your signature wasn't accepted."
          "rate-limited"     → "sentiments.cc is busy. Wait a minute and retry."
          unknown / fallback → "sentiments.cc reported an issue we don't recognize."

        Buttons (exactly):
          - Primary "Restart setup" — clears pending state and routes to
            Welcome. Re-discovery picks the best path again.
          - NO secondary actions, NO "keep watching" — the propagation
            window already expired; more waiting won't help.

        Subtle hints:
          - No raw error dump, no stack trace, no internal codes shown
            to the user.
          - No links to docs / support inside this card — restart is the
            single useful action.
        """
        assert isinstance(gs.trouble, VerifyTimeout)
        s = self.strings()
        return VerifyTroubleView(
            title=s["title"],
            subhint=s["subhint"],
            restart_button=s["restart_button"],
            message=self.message_for(gs.trouble.error_code),
        )
