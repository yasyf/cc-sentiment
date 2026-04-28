from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import (
    Event,
    MethodPicked,
    UsernameSubmitted,
)
from cc_sentiment.onboarding.state import SshMethod
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import InlineUsernameRow
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow


@dataclass(frozen=True)
class State(BaseState):
    pass


class SshMethodView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    SshMethodView > Card { min-width: 60; max-width: 70; }
    SshMethodView Center > Button#gist-btn { width: auto; margin: 1 0 0 0; }
    SshMethodView Static.action-subline {
        width: 100%;
        color: $text-muted;
        margin: 0 0 1 0;
    }
    SshMethodView LinkRow#gh-add-link { margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        username_label: str,
        username_placeholder: str,
        gist_button: str,
        gist_subline: str,
        gist_subline_no_user: str,
        gh_add_link: str,
        gh_add_subline_authed: str,
        gh_add_subline_manual: str,
        username: str,
        show_username_row: bool,
        gh_authenticated: bool,
    ) -> None:
        super().__init__()
        self.title = title
        self.body = body
        self.username_label = username_label
        self.username_placeholder = username_placeholder
        self.gist_button = gist_button
        self.gist_subline = gist_subline
        self.gist_subline_no_user = gist_subline_no_user
        self.gh_add_link = gh_add_link
        self.gh_add_subline_authed = gh_add_subline_authed
        self.gh_add_subline_manual = gh_add_subline_manual
        self.username = username
        self.show_username_row = show_username_row
        self.gh_authenticated = gh_authenticated

    def compose_card(self) -> ComposeResult:
        yield Body(self.body)
        if self.show_username_row:
            yield InlineUsernameRow(
                current=self.username,
                label=self.username_label,
                placeholder=self.username_placeholder,
            )
        yield Center(Button(self.gist_button, id="gist-btn", variant="primary"))
        yield Static(
            self.gist_subline.format(username=self.username) if self.username else self.gist_subline_no_user,
            id="gist-subline",
            classes="action-subline",
        )
        link_classes = "" if self.gh_authenticated else "muted"
        yield LinkRow(self.gh_add_link, id="gh-add-link", classes=link_classes)
        yield Static(
            self.gh_add_subline_authed if self.gh_authenticated else self.gh_add_subline_manual,
            id="gh-add-subline",
            classes="action-subline",
        )

    def on_mount(self) -> None:
        self.query_one("#gist-btn", Button).focus()

    @on(InlineUsernameRow.Submitted)
    def _username(self, event: InlineUsernameRow.Submitted) -> None:
        if event.value:
            self.dismiss(UsernameSubmitted(username=event.value))

    @on(Button.Pressed, "#gist-btn")
    def _gist(self) -> None:
        self.dismiss(MethodPicked(method=SshMethod.GIST))

    @on(LinkRow.Pressed, "#gh-add-link")
    def _gh_add(self) -> None:
        self.dismiss(MethodPicked(method=SshMethod.GH_ADD))


class SshMethodScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.SSH_METHOD)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "Where should we publish your signature?",
            "body": (
                "We post your signature somewhere public so "
                "sentiments.cc can confirm uploads are really from you."
            ),
            "username_label": "GitHub username",
            "username_placeholder": "yasyf",
            "gist_button": "Publish as a gist",
            "gist_subline": "Public gist on github.com/{username}. Delete it any time.",
            "gist_subline_no_user": "Public gist on github.com — enter your username above.",
            "gh_add_link": "Add it to GitHub →",
            "gh_add_subline_authed": "We'll add it for you.",
            "gh_add_subline_manual": "You'll paste it into github.com/settings/keys.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Dedicated method picker after the user has picked an existing SSH
        key. Two methods, gist is the default. May also show an inline
        username input when the user picked an existing SSH key but we
        still don't know their GitHub username (per plan: "If username
        is missing, ask inline on this method screen").

        Layout (centered card, ~60 columns; username row appears only
        when missing):
          ╭─ Where should we publish your signature? ─╮  [DRAFT title]
          │                                            │
          │  We post your signature somewhere          │  (body, explains
          │  public so sentiments.cc can confirm       │   why we're asking)
          │  uploads are really from you.              │
          │                                     │
          │  GitHub username                    │      (only when missing)
          │  [ yasyf____________________ ]      │
          │                                     │
          │       [ Publish as a gist ]         │      [DRAFT primary label]
          │       Public gist on github.com/    │      [DRAFT sub-line]
          │       <username>. Delete it any time│
          │                                     │
          │       Add it to GitHub →            │      [DRAFT secondary label]
          │       (gh authed)                   │      [DRAFT sub-line variants]
          │       We'll add it for you.         │
          │       (no gh)                       │
          │       You'll paste it into          │
          │       github.com/settings/keys.     │
          ╰─────────────────────────────────────╯

        Path-dependent rendering — read inline:
          - The picked SSH key (`gs.selected.key`) drives the gist URL
            preview and the "Add it to GitHub" target.
          - The inline username input appears iff
            `not gs.identity.has_username`. Pre-fills with whatever's
            already there if non-empty.
          - The "Add it to GitHub →" sub-line picks GH_ADD_SUBLINE_AUTHED
            when `caps.gh_authenticated`, else GH_ADD_SUBLINE_MANUAL.

        Buttons (exactly):
          - Optional username input (only when missing).
          - Primary "Publish as a gist" — focused. Routes to Publish.
          - Secondary "Add it to GitHub →" — routes to GhAdd.
          - No third option, no comparison table, no help link.

        De-emphasis when not gh-authed (per plan Q&A "GitHub add for
        existing SSH"):
          The "Add it to GitHub →" link uses a more muted color and the
          sub-line clearly states it will be manual. Never red, never
          alarming, just clearly the second-best path.

        Subtle hints:
          - Tab/Shift-Tab moves between the two options; Enter activates.
          - Plan: "default gist; explain tradeoffs" — sub-lines under
            each option carry the tradeoff in one line each.
        """
        return SshMethodView(
            **self.strings(),
            username=gs.identity.github_username,
            show_username_row=not gs.identity.has_username,
            gh_authenticated=caps.gh_authenticated,
        )
