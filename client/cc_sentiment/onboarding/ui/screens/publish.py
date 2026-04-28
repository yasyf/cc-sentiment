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

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title": "One more step",
            "body": (
                "Create a public GitHub gist with what we copied to your "
                "clipboard. We'll find it automatically."
            ),
            "key_preview_title": "Your signature",
            "open_button": "Open GitHub",
            "copy_again_link": "Copy again",
            "no_github_link": "I don't use GitHub →",
            "watch_label": "Watching for your gist…",
            "fallback_intro": (
                "Copy your signature below, then paste it into a new public gist."
            ),
            "fallback_confirm_button": "I've created the gist",
            "rate_limit_note": "GitHub busy. Retrying.",
        }

    def render(self) -> t.Screen:
        """
        The manual-gist publish screen — `GistInstructionCard` per plan.
        Show the key, give the user one obvious primary action (we already
        copied it and opened a tab for them), and quietly watch.

        Layout (clipboard + browser worked, ~70 columns):
          ╭─ One more step ────────────────────────────╮      (PUBLISH_TITLE)
          │                                            │
          │  Create a public GitHub gist with what we  │      (PUBLISH_BODY)
          │  copied to your clipboard. We'll find it   │
          │  automatically.                            │
          │                                            │
          │  ╭ Your signature ────────────────────────╮│      (was PUBLISH_KEY_PREVIEW_TITLE)
          │  │ ssh-ed25519 AAAA…cc-sentiment          ││
          │  ╰────────────────────────────────────────╯│
          │                                            │
          │       [ Open GitHub ]                      │      (PUBLISH_OPEN_LABEL)
          │       Copy again                           │      (PUBLISH_COPY_AGAIN_LABEL)
          │       I don't use GitHub →                 │      (PUBLISH_NO_GITHUB_LINK)
          │                                            │
          │  ⠹ Watching for your gist…                 │      (PUBLISH_WATCH_LABEL)
          ╰────────────────────────────────────────────╯

        Buttons (exactly — per plan "Open GitHub, quiet Copy again,
        optional quiet I don't use GitHub"):
          - Primary "Open GitHub" — calls app.open_url for
            https://gist.github.com/new.
          - Quiet "Copy again" — re-copies via app.copy_to_clipboard.
          - Quiet "I don't use GitHub →" — only shown when GPG is
            available; routes to Email.

        Layout (clipboard or browser failed — fallback panel):
          Above the actions, a prominent fallback panel:
            ╭─ Manual copy ──────────────────────────────╮
            │ Copy the public key below, then paste it   │      (MANUAL_GIST_INTRO_NO_CLIPBOARD)
            │ into a new public gist.                    │
            │                                            │
            │   ssh-ed25519 AAAA…cc-sentiment            │
            │                                            │
            │   https://gist.github.com/new              │
            │                                            │
            │       [ I've created the gist ]            │      [DRAFT confirm label]
            ╰────────────────────────────────────────────╯
          The watcher does NOT start until the user clicks the confirm
          button (per plan Q&A: "Both clipboard+browser fail — Wait for
          confirmation before polling").

        Resume behavior (came from saved pending):
          Auto re-copy + auto re-open browser on mount, then watch
          silently (per plan Q&A: "Pending gist resume — Re-copy and
          reopen"). The user shouldn't notice they were resumed.

        Subtle hints:
          - No URL input, no Check Now, no elapsed/debug rows
            (per plan: forbidden UX terms).
          - On gist API rate-limit while polling, a tiny muted note
            appears under the watcher row: "GitHub busy — retrying."
            Polling continues (per plan Q&A: "Subtle warning while
            continuing").
          - No eager verify before a candidate gist exists (per plan).
        """
        ...
