from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    has_brew: bool = False
    needs_ssh: bool = False


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

    def render(self) -> t.Screen:
        """
        Final-resort screen when neither ssh-keygen nor GPG is available
        and we can't proceed. Honest, helpful, suggests the fix.

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

        Install hint (selectable / copyable monospaced line):
          - macOS or has_brew:    BLOCKED_INSTALL_HINT_BREW
              "  brew install gnupg"
          - else:                 BLOCKED_INSTALL_HINT_GENERIC
              "Install OpenSSH or GPG, then return."

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
        ...
