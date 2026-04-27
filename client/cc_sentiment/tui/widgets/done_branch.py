from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widgets import Button, Static

from cc_sentiment.tui.setup_state import Tone
from cc_sentiment.tui.widgets.card import Card
from cc_sentiment.tui.widgets.step_actions import StepActions

WHAT_GETS_SENT_TEXT = "Timestamp, sentiment score, model, turn count, and aggregate metadata."
PRIVACY_TEXT = "Stats are aggregated. Your conversations are not uploaded."
SIGNING_TEXT = "Private key: stored on this device"
LOOKUP_HELPER_TEXT = "Used only to verify signatures; public stats stay aggregate."
SETTINGS_PRIMARY_LABEL = "Go to settings"


class DoneBranch(Vertical):
    DEFAULT_CSS = """
    DoneBranch > Static { margin: 0 0 1 0; }
    DoneBranch Button.-primary:focus { text-style: bold; }
    """

    public_location: reactive[str] = reactive("", recompose=True)
    lookup_value: reactive[str] = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        yield Card(
            Static(
                f"Public key lookup: {self.public_location or 'unknown'}",
                id="done-location",
                classes=Tone.SUCCESS.value,
            ),
            Static(
                f"Verification handle: {self.lookup_value}" if self.lookup_value else "",
                id="done-lookup",
                classes=Tone.MUTED.value,
            ),
            Static(
                LOOKUP_HELPER_TEXT,
                id="done-verify",
                classes=Tone.MUTED.value,
            ),
            title="Verification",
            id="done-verification-card",
            classes=f"done-card {Tone.SUCCESS.value}",
        )
        yield Card(
            Static(
                PRIVACY_TEXT,
                id="done-privacy",
                classes=Tone.MUTED.value,
            ),
            title="Privacy",
            id="done-privacy-card",
            classes="done-card",
        )
        yield Card(
            Static(
                SIGNING_TEXT,
                id="done-signing",
                classes=Tone.MUTED.value,
            ),
            title="Signing",
            id="done-signing-card",
            classes="done-card",
        )
        yield Card(
            Static(
                WHAT_GETS_SENT_TEXT,
                id="done-payload",
                classes=Tone.MUTED.value,
            ),
            title="What gets sent",
            id="done-payload-card",
            classes="done-card",
        )
        yield StepActions(
            primary=Button(SETTINGS_PRIMARY_LABEL, id="done-btn", variant="primary"),
        )
