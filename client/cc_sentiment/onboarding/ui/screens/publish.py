from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class PublishScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.PUBLISH)

    def render(self) -> t.Screen:
        """
        The manual-gist publish screen. Show the key, give the user one
        obvious action (open the gist page with the key already on their
        clipboard), and quietly watch in the background.

        Layout (clipboard + browser worked, ~70 columns):
          ╭─ One more step ────────────────────────────╮
          │                                            │
          │  We copied your verification key. Paste    │
          │  it into the new gist GitHub just opened.  │
          │                                            │
          │  ╭ Verification key ──────────────────────╮│
          │  │ ssh-ed25519 AAAA…cc-sentiment          ││
          │  ╰────────────────────────────────────────╯│
          │                                            │
          │       [ Open GitHub again ]                │
          │       Copy again                           │
          │       I don't use GitHub →                 │
          │                                            │
          │  ⠹ Watching for your gist…                 │
          ╰────────────────────────────────────────────╯

        Actions:
          - Primary "Open GitHub again" — calls app.open_url for
            https://gist.github.com/new.
          - Quiet "Copy again" — re-copies via app.copy_to_clipboard.
          - Quiet "I don't use GitHub →" — routes to Email (if GPG
            available) or Blocked otherwise.

        Watcher row:
          Subtle spinner + "Watching for your gist…". When the watcher
          finds the gist, the screen advances to Done without any
          "found it!" affordance — just the transition.

        Layout (clipboard or browser failed — fallback panel):
          Above the actions, a prominent fallback panel:
            ╭─ Manual copy ──────────────────────────────╮
            │ We couldn't reach your clipboard or browser│
            │ Copy this key, then visit:                 │
            │   https://gist.github.com/new              │
            │                                            │
            │   ssh-ed25519 AAAA…cc-sentiment            │
            │                                            │
            │       [ I've created the gist ]            │
            ╰────────────────────────────────────────────╯
          The watcher does NOT start until the user clicks the confirm
          button.

        Resume behavior (came from saved pending):
          Auto re-copy + auto re-open browser on mount, then watch
          silently. The user shouldn't notice they were resumed.

        Subtle hints:
          - "Open GitHub again" tells the user we already opened it once.
          - No URL input, no "check this gist URL" button, no debug rows.
        """
        ...
