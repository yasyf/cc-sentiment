from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class BlockedScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.BLOCKED)

    def render(self) -> t.Screen:
        """
        Final-resort screen when neither ssh-keygen nor GPG is available.
        Be honest, be helpful, suggest the fix, don't trap the user.

        Layout (card, ~60 columns):
          ╭─ We need an SSH client or GPG ─────╮
          │                                    │
          │  cc-sentiment signs your uploads   │
          │  so we know they're yours. We      │
          │  couldn't find OpenSSH or GPG on   │
          │  this system.                      │
          │                                    │
          │  brew install openssh              │  ← copyable hint, varies by host
          │                                    │
          │       [ Open install guide ]       │
          │       [ Quit ]                     │
          ╰────────────────────────────────────╯

        Install hint (single line, monospaced, selectable):
          - macOS / has_brew: "brew install openssh" (or gnupg, depending
            on what's missing).
          - Linux: "sudo apt install openssh-client" or generic
            "install OpenSSH or GPG, then re-run cc-sentiment setup".
          - Windows / other: link-only.

        Actions:
          - Primary "Open install guide" — app.open_url to the relevant
            docs page (SSH if neither is present, GPG if SSH is present
            but the GPG path was the only one available).
          - Secondary "Quit" — dismisses the dialog.

        Subtle hints:
          - The install hint snippet is selectable and copyable.
          - No big red error UI, no "we can't help you" tone — we're
            just telling them what to install.
        """
        ...
