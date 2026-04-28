# DELETE_AFTER_SCREENS: Screen-specific composite for DoneScreen — not a
# generic widget. When DoneScreen is built, inline this composition into its
# compose_card() and delete this file. The PAYLOAD_SAMPLE / EXCLUSION_TEXT /
# label constants move with it.
from __future__ import annotations

import json

from rich.syntax import Syntax
from textual.app import ComposeResult
from textual.containers import Center, Vertical
from textual.reactive import reactive
from textual.widgets import Button, Static

from cc_sentiment.tui.legacy.setup_state import Tone
from cc_sentiment.tui.widgets.card import Card

PAYLOAD_SAMPLE = json.dumps(
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
PAYLOAD_EXCLUSION_TEXT = "No transcript text, prompts, tool inputs, tool outputs, or code."
WHAT_GETS_SENT_TEXT = PAYLOAD_EXCLUSION_TEXT
SETTINGS_PRIMARY_LABEL = "Start ingesting"


class DoneBranch(Vertical):
    DEFAULT_CSS = """
    DoneBranch > Static { margin: 0 0 1 0; }
    DoneBranch > Center { margin: 1 0 0 0; }
    DoneBranch Button.-primary:focus { text-style: bold; }
    """

    verification: reactive[str] = reactive("", recompose=True)

    def compose(self) -> ComposeResult:
        yield Card(
            Static(
                self.verification or "Verification: ready",
                id="done-verification",
                classes=Tone.SUCCESS.value,
            ),
            title="Verification",
            id="done-verification-card",
            classes=f"done-card {Tone.SUCCESS.value}",
        )
        yield Card(
            Static(
                Syntax(PAYLOAD_SAMPLE, "json", theme="ansi_dark", background_color="default"),
                id="done-payload-sample",
            ),
            Static(
                PAYLOAD_EXCLUSION_TEXT,
                id="done-payload-exclusion",
                classes=Tone.MUTED.value,
            ),
            title="What gets sent",
            id="done-payload-card",
            classes="done-card",
        )
        yield Center(Button(SETTINGS_PRIMARY_LABEL, id="done-btn", variant="primary"))
