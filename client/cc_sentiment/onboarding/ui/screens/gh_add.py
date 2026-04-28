from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class GhAddScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.GH_ADD)

    def render(self) -> t.Screen:
        """
        Add an existing SSH key to GitHub. Two flavors based on whether
        `gh` is authenticated — the silent automatic path and the polished
        manual path.

        Layout (gh authed — automatic, ~60 columns):
          ╭─ Adding to GitHub… ────────────────╮
          │                                    │
          │  ⠹ Uploading via gh CLI…           │
          │                                    │
          ╰────────────────────────────────────╯
          Pure spinner, no buttons. Same shape as Working.

        Layout (no gh auth — manual, ~70 columns):
          ╭─ Add this key to GitHub ───────────────────╮
          │                                            │
          │  We copied the key. Paste it on:           │
          │    github.com/settings/ssh/new             │
          │                                            │
          │  ╭ Verification key ──────────────────────╮│
          │  │ ssh-ed25519 AAAA…                      ││
          │  ╰────────────────────────────────────────╯│
          │                                            │
          │       [ Open GitHub settings ]             │
          │       Copy again                           │
          │                                            │
          │  ⠹ Watching for the new key…               │
          ╰────────────────────────────────────────────╯

        Actions (manual flavor):
          - Primary "Open GitHub settings" — app.open_url for
            github.com/settings/ssh/new (with title prefilled if possible).
          - Quiet "Copy again".
          - Same fallback panel + confirmation gate as Publish if both
            clipboard and browser fail.

        Watcher:
          Polls GitHub for the key fingerprint until it appears, or times
          out (then → Trouble).

        Subtle hints:
          - The two flavors share Done as the destination but feel
            different — gh-authed is silent, manual gives instructions
            and one obvious primary button.
          - No "advanced" toggle to switch between flavors.
        """
        ...
