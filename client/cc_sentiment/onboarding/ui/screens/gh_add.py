from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import (
    KeyPreview,
    PublishActions,
    WatcherRow,
)
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.pending_status import PendingSpinner


GH_KEYS_URL = "https://github.com/settings/keys/new"


@dataclass(frozen=True)
class State(BaseState):
    pass


class GhAddAutoView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    GhAddAutoView > Card { min-width: 50; max-width: 60; }
    GhAddAutoView Horizontal { width: auto; height: auto; }
    GhAddAutoView Horizontal > PendingSpinner { margin: 0 1 0 0; }
    GhAddAutoView Static#status { width: auto; color: $text-muted; }
    """

    def __init__(self, *, title: str, status: str) -> None:
        super().__init__()
        self.title = title
        self._status = status

    def compose_card(self) -> ComposeResult:
        with Horizontal():
            yield PendingSpinner()
            yield Static(self._status, id="status")


class GhAddManualView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    GhAddManualView > Card { min-width: 60; max-width: 80; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        key_text: str,
        key_preview_title: str,
        open_label: str,
        copy_label: str,
        watch_label: str,
        rate_limit_text: str,
    ) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.key_text = key_text
        self.key_preview_title = key_preview_title
        self.open_label = open_label
        self.copy_label = copy_label
        self.watch_label = watch_label
        self.rate_limit_text = rate_limit_text

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_text, id="body")
        yield KeyPreview(self.key_text, title=self.key_preview_title)
        yield PublishActions(
            open_url=GH_KEYS_URL,
            show_no_github=False,
            open_label=self.open_label,
            copy_label=self.copy_label,
        )
        yield WatcherRow(self.watch_label, rate_limit_text=self.rate_limit_text)


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
        s = self.strings()
        if caps.gh_authenticated:
            return GhAddAutoView(title=s["title_auto"], status=s["status_auto"])
        key_text = gs.selected.key.fingerprint if gs.selected and gs.selected.key else ""
        return GhAddManualView(
            title=s["title_manual"],
            body=s["body_manual"],
            key_text=key_text,
            key_preview_title=s["key_preview_title"],
            open_label=s["open_button"],
            copy_label=s["copy_again_link"],
            watch_label=s["watch_label"],
            rate_limit_text=s["rate_limit_note"],
        )
