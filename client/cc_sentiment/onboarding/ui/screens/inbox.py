from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import (
    Event,
    RecheckRequested,
    TroubleChoseEmail,
)
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import WatcherRow
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow


@dataclass(frozen=True)
class State(BaseState):
    pass


class InboxView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    InboxView > Card { min-width: 60; max-width: 70; }
    InboxView LinkRow { margin: 1 0 0 0; }
    """

    SECONDARY_LINKS_DELAY_SECONDS: ClassVar[float] = 60.0
    STATUS_ROTATION_SECONDS: ClassVar[float] = 10.0

    def __init__(
        self,
        *,
        title: str,
        body: str,
        waiting_label: str,
        rotation: tuple[str, ...],
        different_email_label: str,
        recheck_label: str,
        rate_limit_text: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.waiting_label = waiting_label
        self.rotation = rotation
        self.different_email_label = different_email_label
        self.recheck_label = recheck_label
        self.rate_limit_text = rate_limit_text
        self._rotation_index = 0

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_text, id="body")
        yield WatcherRow(self.waiting_label, id="polling-status", rate_limit_text=self.rate_limit_text)
        yield LinkRow(self.different_email_label, id="different-email-link", classes="muted")
        yield LinkRow(self.recheck_label, id="recheck-link", classes="muted")

    def on_mount(self) -> None:
        self.query_one("#different-email-link", LinkRow).display = False
        self.query_one("#recheck-link", LinkRow).display = False
        self.set_timer(self.SECONDARY_LINKS_DELAY_SECONDS, self._reveal_secondary_links)
        self.set_interval(self.STATUS_ROTATION_SECONDS, self._rotate_status)

    def _reveal_secondary_links(self) -> None:
        self.query_one("#different-email-link", LinkRow).display = True
        self.query_one("#recheck-link", LinkRow).display = True

    def _rotate_status(self) -> None:
        self._rotation_index = (self._rotation_index + 1) % len(self.rotation)
        self.query_one("#polling-status", WatcherRow).text = self.rotation[self._rotation_index]

    @on(LinkRow.Pressed, "#different-email-link")
    def _different_email(self) -> None:
        self.dismiss(TroubleChoseEmail())

    @on(LinkRow.Pressed, "#recheck-link")
    def _recheck(self) -> None:
        self.dismiss(RecheckRequested())


class InboxScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.INBOX)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Check your inbox",
            "body": (
                "Verification email sent to {email}. "
                "Open it, click the link, then return here."
            ),
            "waiting_label": "Waiting for verification…",
            "still_waiting_label": "Still waiting…",
            "taking_a_moment_label": "These sometimes take a moment…",
            "different_email_link": "Send to a different email →",
            "recheck_link": "Check again",
            "rate_limit_note": "Service busy. Retrying soon.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Waiting card shown after the verification email has been sent.
        Replaces the email form so the user knows the request went out
        and now just needs to act on the email.

        Path-dependent rendering — read inline:
          - The displayed email address comes from `gs.identity.email`.

        Layout (centered card, ~60 columns):
          ╭─ Check your inbox ─────────────────╮       [DRAFT title]
          │                                    │
          │  Verification email sent to        │       (OPENPGP_AFTER_SEND,
          │    yasyf@example.com               │        adapted: same wording,
          │  Open it, click the link, then     │        rendered across lines)
          │  return here.                      │
          │                                    │
          │  ⠹ Waiting for verification…       │       [DRAFT polling status]
          ╰────────────────────────────────────╯

        Buttons (exactly — per plan "no Reopen verification or
        resend-primary behavior"):
          - NONE primary. The screen passively polls keys.openpgp.org
            and sentiments.cc until verification succeeds (→ Done) or
            the propagation window expires (→ Trouble).

          - After a polite delay (~60s), two quiet secondary links
            appear, neither primary, both muted:
              · "Send to a different email →"  → routes back to Email.
              · "Re-check now"                  → forces a poll.

        Subtle hints:
          - The spinner is the only animation up to the delay.
          - The polling status line can rotate phrasing every ~10s to
            feel alive without spamming:
              "Waiting for verification…"
              → "Still waiting…"
              → "These sometimes take a moment…"
          - No explicit progress bar; no "X seconds elapsed".
          - On a transient rate-limit during polling, a tiny muted note
            appears: "Service busy — retrying soon." Polling continues.
        """
        s = self.strings()
        return InboxView(
            title=s["title"],
            body=s["body"].format(email=gs.identity.email),
            waiting_label=s["waiting_label"],
            rotation=(s["waiting_label"], s["still_waiting_label"], s["taking_a_moment_label"]),
            different_email_label=s["different_email_link"],
            recheck_label=s["recheck_link"],
            rate_limit_text=s["rate_limit_note"],
        )
