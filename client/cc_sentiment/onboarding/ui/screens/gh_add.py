from __future__ import annotations

from dataclasses import dataclass

from textual import screen as t

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen


@dataclass(frozen=True)
class State(BaseState):
    pass


class GhAddScreen(Screen[State]):
    State = State

    @classmethod
    def matcher(cls) -> GlobalState:
        return GlobalState(stage=Stage.GH_ADD)

    @classmethod
    def strings(cls) -> dict[str, str]:
        return {
            "title_auto": "Adding to GitHub…",
            "status_auto": "Adding your signature…",
            "title_manual": "Add your signature to GitHub",
            "body_manual": "We copied your signature. Paste it at github.com/settings/keys.",
            "key_preview_title": "Your signature",
            "open_button": "Open GitHub",
            "copy_again_link": "Copy again",
            "watch_label": "Watching for it on GitHub…",
            "fallback_confirm_button": "I've added it",
            "rate_limit_note": "GitHub busy. Retrying.",
        }

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
        """
        Add an existing SSH key to a GitHub account. Two flavors based on
        whether `gh` is authenticated (per plan Q&A: "Show automatic/
        manual; de-emphasize manual if not gh-authenticated").

        Path-dependent rendering — read inline:
          - The selected SSH key (`gs.selected.key`) drives the preview
            block + the polling fingerprint.
          - The username in the GitHub watcher comes from
            `gs.identity.github_username`.
          - Auto vs manual flavor is picked from `caps.gh_authenticated`:
              authed → Layout (gh authed) below.
              else   → Layout (no gh auth) below.

        Layout (gh authed — silent automatic, ~50 columns):
          ╭─ Adding to GitHub… ────────────────╮       [DRAFT title]
          │                                    │
          │  ⠹ Adding your signature…          │       [DRAFT status]
          │                                    │
          ╰────────────────────────────────────╯
          No buttons. Same shape as Working.

        Layout (no gh auth — manual, ~70 columns):
          ╭─ Add your signature to GitHub ─────────────╮       [DRAFT title]
          │                                            │
          │  We copied your signature. Paste it at     │       [DRAFT body]
          │  github.com/settings/keys.                 │
          │                                            │
          │  ╭ Your signature ────────────────────────╮│       (was PUBLISH_KEY_PREVIEW_TITLE,
          │  │ ssh-ed25519 AAAA…                      ││        reused)
          │  ╰────────────────────────────────────────╯│
          │                                            │
          │       [ Open GitHub ]                      │       (reuses PUBLISH_OPEN_LABEL,
          │       Copy again                           │        PUBLISH_COPY_AGAIN_LABEL)
          │                                            │
          │  ⠹ Watching for it on GitHub…              │       [DRAFT watcher line]
          ╰────────────────────────────────────────────╯

        Buttons (exactly):
          - Auto flavor: NONE (matches Working — no buttons).
          - Manual flavor:
              · Primary "Open GitHub" — app.open_url for
                github.com/settings/keys/new (with title prefilled if
                possible).
              · Quiet "Copy again" — re-copies via app.copy_to_clipboard.
              · Same fallback panel + confirmation gate as Publish if
                both clipboard and browser fail.

        Watcher (manual flavor):
          Polls GitHub for the key fingerprint until it appears, or
          times out (then → Trouble).

        Subtle hints:
          - The two flavors share Done as the destination but feel
            different — gh-authed is silent, manual gives instructions
            and one obvious primary button.
          - No "advanced" toggle to switch between flavors.
          - Gist API rate-limit while polling shows a subtle warning
            under the watcher row, polling continues.
        """
        ...
