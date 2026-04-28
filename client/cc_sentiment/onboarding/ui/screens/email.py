from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

import anyio
import httpx
from textual import on, work
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Input, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import EmailSent, Event
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.signing import GPGBackend, KeyDiscovery
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen


@dataclass(frozen=True)
class State(BaseState):
    pass


class EmailView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    EmailView > Card { min-width: 60; max-width: 70; }
    EmailView Input#email-input { margin: 0 0 1 0; }
    EmailView Center > Button#send-btn { width: auto; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        prefilled_email: str,
        send_label: str,
        sending_label: str,
        error_empty: str,
        error_unreachable: str,
        gpg_fpr: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.prefilled_email = prefilled_email
        self.send_label = send_label
        self.sending_label = sending_label
        self.error_empty = error_empty
        self.error_unreachable = error_unreachable
        self.gpg_fpr = gpg_fpr

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_text)
        yield Input(value=self.prefilled_email, id="email-input")
        yield Static("", id="email-status")
        yield Center(Button(self.send_label, id="send-btn", variant="primary"))

    def on_mount(self) -> None:
        self.query_one("#email-status", Static).display = False
        self.query_one("#email-input", Input).focus()

    def _set_status(self, text: str) -> None:
        status = self.query_one("#email-status", Static)
        status.update(text)
        status.display = bool(text)

    @on(Button.Pressed, "#send-btn")
    @on(Input.Submitted, "#email-input")
    def _send(self) -> None:
        email = self.query_one("#email-input", Input).value.strip()
        if not email:
            self._set_status(self.error_empty)
            return
        button = self.query_one("#send-btn", Button)
        button.disabled = True
        button.label = self.sending_label
        self._send_worker(email)

    @work(exit_on_error=False)
    async def _send_worker(self, email: str) -> None:
        try:
            armor = await anyio.to_thread.run_sync(
                lambda: GPGBackend(fpr=self.gpg_fpr).public_key_text()
            )
            token, statuses = await anyio.to_thread.run_sync(
                KeyDiscovery.upload_openpgp_key, armor,
            )
            unverified = [a for a, s in statuses.items() if s == "unpublished"] or [email]
            await anyio.to_thread.run_sync(
                KeyDiscovery.request_openpgp_verify, token, unverified,
            )
        except (httpx.HTTPError, OSError):
            self._set_status(self.error_unreachable)
            button = self.query_one("#send-btn", Button)
            button.disabled = False
            button.label = self.send_label
            return
        self.dismiss(EmailSent())


class EmailScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.EMAIL)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "What email should we use?",
            "body": "We'll send a one-time verification link.",
            "send_button": "Send link",
            "sending_label": "Sending…",
            "error_empty": "Enter an email you can open right now.",
            "error_unreachable": "Couldn't reach keys.openpgp.org. Try again in a moment.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Email entry form for the OpenPGP path. Pre-fill if we already
        know a usable email (from gh, recent commits, or the picked GPG
        key); otherwise blank.

        Path-dependent rendering — read inline:
          - Pre-fill the input from `gs.identity.email` iff
            `gs.identity.email_usable`. Otherwise leave it blank.

        Layout (centered card, ~60 columns):
          ╭─ What email should we use? ────────╮       (ALTERNATE_TITLE)
          │                                    │
          │  We'll send a one-time             │       (ALTERNATE_BODY)
          │  verification link.                │
          │                                    │
          │  [ yasyf@example.com_____________ ]│
          │                                    │
          │       [ Send link ]                │       (ALTERNATE_CTA)
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Input — focused on mount; pre-filled when we have a confident
            email address (from gh-mined commit email or the chosen GPG
            key).
          - Primary "Send link" — uploads the public key to
            keys.openpgp.org and requests verification for this email.
          - No other actions. The "I don't use GitHub →" link does NOT
            appear here — this screen is the GPG branch.

        State variants (inline messages below the input):
          - Sending: button disabled, label becomes "Sending…", tiny
            spinner alongside.
          - Empty:        "Enter an email you can open right now."
          - Network err:  inline message — "Couldn't reach
              keys.openpgp.org. Try again in a moment." Button
              re-enables (per plan: "Upload/email request failures
              retry in place").

        Subtle hints:
          - One field, one button. No "use a different email server"
            toggle, no PGP-curious explainer.
          - Words "GPG" / "OpenPGP" never appear in the user-facing copy
            on this screen.
        """
        s = self.strings()
        return EmailView(
            title=s["title"],
            body=s["body"],
            prefilled_email=gs.identity.email if gs.identity.email_usable else "",
            send_label=s["send_button"],
            sending_label=s["sending_label"],
            error_empty=s["error_empty"],
            error_unreachable=s["error_unreachable"],
            gpg_fpr=gs.selected.key.fingerprint if gs.selected and gs.selected.key else "",
        )
