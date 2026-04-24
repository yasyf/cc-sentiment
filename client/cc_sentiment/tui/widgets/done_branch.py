from __future__ import annotations

from contextlib import suppress
from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import Button, Static

from cc_sentiment.tui.setup_state import Tone, VerificationState
from cc_sentiment.tui.widgets.card import Card
from cc_sentiment.tui.widgets.pending_status import PendingStatus
from cc_sentiment.tui.widgets.step_actions import StepActions


class DoneBranch(Vertical):
    verification_state: reactive[VerificationState] = reactive(VerificationState.VERIFIED, recompose=True)
    verification_ok: reactive[bool] = reactive(True, recompose=True)
    summary_text: reactive[str] = reactive("", recompose=True)
    identify_text: reactive[str] = reactive("", recompose=True)
    process_text: reactive[str] = reactive("", recompose=True)
    eta_text: reactive[str] = reactive("", recompose=True)
    instructions_text: reactive[str] = reactive("", recompose=True)
    pending_label: reactive[str] = reactive("")

    def __init__(self, sample_payload: Callable[[], str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.sample_payload = sample_payload

    def visible_verification_state(self) -> VerificationState:
        return (
            self.verification_state
            if self.verification_ok or self.verification_state is not VerificationState.VERIFIED
            else VerificationState.PENDING
        )

    def verification_message(self) -> tuple[str, Tone]:
        match self.visible_verification_state():
            case VerificationState.VERIFIED:
                return "You're set up. Ready to upload.", Tone.SUCCESS
            case VerificationState.PENDING:
                return "The dashboard still can't verify this public key.", Tone.WARNING
            case VerificationState.FAILED:
                return "We still couldn't verify this public key.", Tone.ERROR

    def compose(self) -> ComposeResult:
        verification_text, verification_tone = self.verification_message()
        summary_text = self.summary_text or "Signed in with your selected key."

        match self.visible_verification_state():
            case VerificationState.VERIFIED:
                yield Card(
                    Static(summary_text, id="done-summary", classes=Tone.SUCCESS.value),
                    Static(verification_text, id="done-verify", classes=Tone.SUCCESS.value),
                    title="verified",
                    id="done-summary-card",
                    classes=f"done-card {Tone.SUCCESS.value}",
                )
                yield StepActions(
                    primary=Button("Contribute my stats", id="done-btn", variant="primary"),
                )
                yield Static(
                    "Only signed stats leave your Mac, one row per conversation.",
                    id="done-payload-lead",
                    classes=Tone.MUTED.value,
                )
                yield Card(
                    Static(self.sample_payload(), id="done-payload", classes="code"),
                    title="What actually gets sent",
                    id="done-payload-card",
                    classes="done-card",
                )
                yield Static(self.identify_text, id="done-identify", classes=Tone.MUTED.value)
                yield Static(self.process_text, id="done-process", classes=Tone.MUTED.value)
                yield Static(self.eta_text, id="done-eta", classes=Tone.MUTED.value)
            case VerificationState.PENDING:
                yield Card(
                    Static(summary_text, id="done-summary", classes=Tone.WARNING.value),
                    Static(verification_text, id="done-verify", classes=verification_tone.value),
                    title="pending",
                    id="done-summary-card",
                    classes=f"done-card {Tone.WARNING.value}",
                )
                yield PendingStatus(self.pending_label, id="pending-status")
                yield Card(
                    Static(self.instructions_text, id="done-instructions", classes=Tone.MUTED.value),
                    title="Next steps",
                    id="done-instructions-card",
                    classes="done-card",
                )
                yield StepActions(
                    Button("Exit, continue later", id="pending-exit", variant="default"),
                    primary=Button("Retry now", id="pending-retry", variant="primary"),
                )
            case VerificationState.FAILED:
                yield Card(
                    Static(summary_text, id="done-summary", classes=Tone.ERROR.value),
                    Static(verification_text, id="done-verify", classes=verification_tone.value),
                    title="failed",
                    id="done-summary-card",
                    classes=f"done-card {Tone.ERROR.value}",
                )
                yield StepActions(
                    Button("Exit", id="failed-exit", variant="default"),
                    primary=Button("Retry", id="failed-retry", variant="primary"),
                )
                yield Card(
                    Static(self.instructions_text, id="done-instructions", classes=Tone.MUTED.value),
                    title="Next steps",
                    id="done-instructions-card",
                    classes="done-card",
                )

    def watch_pending_label(self, label: str) -> None:
        with suppress(NoMatches):
            self.query_one("#pending-status", PendingStatus).label = label
