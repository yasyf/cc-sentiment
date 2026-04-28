from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Input, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow


@dataclass(frozen=True)
class State(BaseState):
    pass


class UserFormView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    UserFormView > Card { min-width: 60; max-width: 70; }
    UserFormView Input#username-input { margin: 0 0 1 0; }
    UserFormView Center > Button#continue-btn { width: auto; margin: 0 0 1 0; }
    UserFormView Static#status { width: 100%; min-height: 1; }
    """

    def __init__(
        self,
        *,
        title: str,
        placeholder: str,
        primary_label: str,
        no_github_label: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.placeholder = placeholder
        self.primary_label = primary_label
        self.no_github_label = no_github_label

    def compose_card(self) -> ComposeResult:
        yield Input(placeholder=self.placeholder, id="username-input")
        yield Static("", id="status")
        yield Center(Button(self.primary_label, id="continue-btn", variant="primary"))
        yield LinkRow(self.no_github_label, id="no-github-link", classes="muted")

    def on_mount(self) -> None:
        self.query_one("#status", Static).display = False
        self.query_one("#username-input", Input).focus()


class UserFormScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.USER_FORM)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "What's your GitHub username?",
            "placeholder": "yasyf",
            "primary_button": "Continue",
            "no_github_link": "I don't use GitHub →",
            "error_empty": "Enter your GitHub username, or pick “I don't use GitHub” below.",
            "error_not_found": "GitHub user “{user}” wasn't found.",
            "error_unreachable": "Couldn't reach GitHub. Try again in a moment.",
            "validating": "Validating {user}…",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Username form, shown only as a last resort when ssh-keygen exists
        but we still don't know who the user is on GitHub. One clear
        question, one input, one obvious primary, one quiet escape hatch.

        Layout (centered card, ~60 columns):
          ╭─ What's your GitHub username? ─────╮       (from plan, exact)
          │                                    │
          │  [ yasyf______________________ ]   │       (USERNAME_PLACEHOLDER)
          │                                    │
          │       [ Continue ]                 │       (existing button label)
          │                                    │
          │  I don't use GitHub →              │       (USERNAME_NO_GITHUB_LINK)
          ╰────────────────────────────────────╯

        Buttons (exactly):
          - Input — focused on mount; placeholder shows the example
            username "yasyf" in muted text.
          - Primary "Continue" — validates against the GitHub API and
            routes per capabilities (Working when gh authed, Publish
            otherwise). While in flight, the button shows a tiny spinner
            and disables.
          - Quiet "I don't use GitHub →" — opts out (sets
            github_lookup_allowed=False) and routes to Email if GPG is
            available, otherwise Blocked.

        State variants (inline messages below the input):
          - Empty submit:    USERNAME_ERROR_EMPTY
              "Enter your GitHub username, or pick "I don't use GitHub" below."
          - 404:             USERNAME_ERROR_NOT_FOUND ("GitHub user "{user}"
              wasn't found.") — input refocuses.
          - Network down:    USERNAME_ERROR_UNREACHABLE
              "Couldn't reach GitHub. Try again in a moment."
              Button stays active so the user just presses Continue
              again (per plan: "Username validation network — Retry
              in place").
          - Validating:      faint "Validating yasyf…" beside the
                             disabled button.

        Subtle hints:
          - No body paragraph above the input — the title IS the question.
          - No tables, no progress bars, no debug.
          - "I don't use GitHub →" is muted; only colored on hover.
        """
        s = self.strings()
        return UserFormView(
            title=s["title"],
            placeholder=s["placeholder"],
            primary_label=s["primary_button"],
            no_github_label=s["no_github_link"],
        )
