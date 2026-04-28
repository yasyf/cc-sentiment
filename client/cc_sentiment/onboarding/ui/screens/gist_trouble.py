from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Input

from cc_sentiment.onboarding import (
    Capabilities,
    GistTimeout,
    Stage,
    State as GlobalState,
)
from cc_sentiment.onboarding.events import (
    Event,
    TroubleChoseEmail,
    TroubleEditUsername,
)
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import InlineUsernameRow
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow


@dataclass(frozen=True)
class State(BaseState):
    pass


class GistTroubleView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    GistTroubleView > Card { min-width: 60; max-width: 70; }
    GistTroubleView Center > Button#submit-btn { width: auto; margin: 0 0 1 0; }
    GistTroubleView LinkRow#email-link { margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        username: str,
        username_label: str,
        username_placeholder: str,
        submit_label: str,
        show_email_link: bool,
        email_label: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.username = username
        self.username_label = username_label
        self.username_placeholder = username_placeholder
        self.submit_label = submit_label
        self.show_email_link = show_email_link
        self.email_label = email_label

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_text, id="body")
        yield InlineUsernameRow(
            current=self.username,
            label=self.username_label,
            placeholder=self.username_placeholder,
        )
        yield Center(Button(self.submit_label, id="submit-btn", variant="primary"))
        if self.show_email_link:
            yield LinkRow(self.email_label, id="email-link", classes="muted")

    @on(Button.Pressed, "#submit-btn")
    @on(Input.Submitted, "#username-input")
    def _submit(self) -> None:
        value = self.query_one("#username-input", Input).value.strip()
        if value:
            self.dismiss(TroubleEditUsername(new_username=value))

    @on(LinkRow.Pressed, "#email-link")
    def _email(self) -> None:
        self.dismiss(TroubleChoseEmail())


class GistTroubleScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.TROUBLE, trouble=GistTimeout())

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Still watching for your gist",
            "body": (
                "GitHub usually takes a moment, but if the username is off "
                "we'll never find it."
            ),
            "username_label": "GitHub username",
            "username_placeholder": "yasyf",
            "submit_button": "Try this username",
            "email_link": "Use email instead →",
            "rate_limit_note": "GitHub busy. Still trying.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Trouble screen for when we've watched for the gist long enough
        and never found it. Most common cause is a typo in the username,
        so we put the username edit inline and offer email as the alternate
        path. No restart link here (per plan: "Keep actions branch-specific;
        no extraneous buttons" — restart belongs to VerifyTrouble only).

        Path-dependent rendering — read inline:
          - The username input is pre-filled with
            `gs.identity.github_username` (the one we've been polling).
          - "Use email instead →" appears iff `caps.has_gpg`.

        Layout (card, ~60 columns):
          ╭─ Still watching for your gist ─────╮       (TROUBLE_TITLE)
          │                                    │
          │  GitHub usually takes a moment,    │       [DRAFT body]
          │  but if the username is off we'll  │
          │  never find it.                    │
          │                                    │
          │  GitHub username                   │
          │  [ yasyf____________________ ]     │
          │  [ Try this username ]             │       [DRAFT submit label]
          │                                    │
          │  Use email instead →               │       [DRAFT email link label]
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Inline username input pre-filled with the username we've
            been polling against.
          - Small "Try this username" button — validates and routes back
            to Publish with the new username.
          - Quiet "Use email instead →" link — routes to Email (only
            shown when GPG is available).
          - NO restart link, NO "keep watching", NO "try a different
            way" (existing buttons are removed per plan).

        Subtle hints:
          - The original watcher is still running in the background — if
            the gist appears while the user is reading this screen, we
            advance to Done without further interaction.
          - No retry counter, no scary error text. The "still watching"
            framing keeps it calm.
          - On gist API rate-limit during the still-running watcher, a
            tiny muted note appears: "GitHub busy — still trying."
        """
        s = self.strings()
        return GistTroubleView(
            title=s["title"],
            body=s["body"],
            username=gs.identity.github_username,
            username_label=s["username_label"],
            username_placeholder=s["username_placeholder"],
            submit_label=s["submit_button"],
            show_email_link=caps.has_gpg,
            email_label=s["email_link"],
        )
