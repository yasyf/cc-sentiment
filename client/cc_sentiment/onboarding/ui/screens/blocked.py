from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Center
from textual.widgets import Button, Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import Event, QuitOnboarding
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen


SSH_DOCS_URL = "https://docs.github.com/en/authentication/connecting-to-github-with-ssh/checking-for-existing-ssh-keys"
GPG_DOCS_URL = "https://gnupg.org/download/index.html"


@dataclass(frozen=True)
class State(BaseState):
    pass


class BlockedView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    BlockedView > Card { min-width: 60; max-width: 70; }
    BlockedView Static#install-hint {
        width: 100%;
        color: $text-muted;
        margin: 1 0 1 0;
    }
    BlockedView Center > Button { width: auto; margin: 0 1 0 1; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        install_hint_brew: str,
        install_hint_generic: str,
        install_button: str,
        quit_button: str,
        has_brew: bool,
        kind: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.body = body
        self.install_hint = install_hint_brew if has_brew else install_hint_generic
        self.install_button = install_button
        self.quit_button = quit_button
        self.kind = kind

    def compose_card(self) -> ComposeResult:
        yield Body(self.body)
        yield Static(self.install_hint, id="install-hint")
        with Center():
            yield Button(
                self.install_button,
                id="install-guide-btn",
                variant="primary",
                classes=self.kind,
            )
            yield Button(self.quit_button, id="quit-btn")

    @on(Button.Pressed, "#install-guide-btn")
    def _open_guide(self) -> None:
        url = SSH_DOCS_URL if self.kind == "ssh" else GPG_DOCS_URL
        self.app.open_url(url)

    @on(Button.Pressed, "#quit-btn")
    def _quit(self) -> None:
        self.dismiss(QuitOnboarding())


class BlockedScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.BLOCKED)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "We need an SSH client or GPG",
            "body": (
                "Your system doesn't have either installed. "
                "Open the install guide, then re-run “cc-sentiment setup”."
            ),
            "install_hint_brew": "  brew install gnupg",
            "install_hint_generic": "Install OpenSSH or GPG, then return.",
            "install_button": "Open install guide",
            "quit_button": "Quit",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Final-resort screen when neither ssh-keygen nor GPG is available
        and we can't proceed. Honest, helpful, suggests the fix.

        Path-dependent rendering — read inline:
          - The install hint snippet is INSTALL_HINT_BREW iff
            `caps.has_brew`, else INSTALL_HINT_GENERIC.
          - The "Open install guide" target picks SSH docs if
            `not caps.has_ssh_keygen`, else GPG docs.

        Layout (card, ~60 columns):
          ╭─ We need an SSH client or GPG ─────╮       (BLOCKED_TITLE)
          │                                    │
          │  Your system doesn't have either   │       (BLOCKED_BODY)
          │  installed. Open the install       │
          │  guide, then re-run                │
          │  "cc-sentiment setup".             │
          │                                    │
          │    brew install gnupg              │       (BLOCKED_INSTALL_HINT_BREW
          │                                    │        or BLOCKED_INSTALL_HINT_GENERIC,
          │                                    │        based on platform)
          │       [ Open install guide ]       │       (existing button label)
          │       [ Quit ]                     │       (existing button label)
          ╰────────────────────────────────────╯

        Buttons (exactly — matches existing screen):
          - Primary "Open install guide" — app.open_url to the relevant
            docs page (SSH if neither is present, GPG if SSH is present
            and only the GPG path was unavailable).
          - Secondary "Quit" — dismisses the dialog.

        Subtle hints:
          - The install hint snippet is selectable and copyable.
          - No big red error UI, no "we can't help you" tone — we're
            just telling them what to install.
        """
        return BlockedView(
            **self.strings(),
            has_brew=caps.has_brew,
            kind="ssh" if not caps.has_ssh_keygen else "gpg",
        )
