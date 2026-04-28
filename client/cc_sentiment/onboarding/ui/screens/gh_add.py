from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import Event
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import (
    FallbackPanel,
    KeyPreview,
    PublishActions,
    WatcherRow,
)
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card import Card
from cc_sentiment.tui.widgets.card_screen import CardScreen
from cc_sentiment.tui.widgets.link_row import LinkRow
from cc_sentiment.tui.widgets.pending_status import PendingSpinner


GH_KEYS_URL = "https://github.com/settings/keys/new"


@dataclass(frozen=True)
class State(BaseState):
    pass


class GhAddAutoView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    GhAddAutoView > Card { min-width: 50; max-width: 60; }
    GhAddAutoView Horizontal { width: auto; height: auto; }
    GhAddAutoView Horizontal > PendingSpinner { margin: 0 1 0 0; }
    GhAddAutoView Static#status { width: auto; color: $text-muted; }
    """

    def __init__(self, *, title_auto: str, status_auto: str) -> None:
        super().__init__()
        self.title = title_auto
        self.status_auto = status_auto

    def compose_card(self) -> ComposeResult:
        with Horizontal():
            yield PendingSpinner()
            yield Static(self.status_auto, id="status")


class GhAddManualView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    GhAddManualView > Card { min-width: 60; max-width: 80; }
    GhAddManualView LinkRow#manual-link { margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title_manual: str,
        body_manual: str,
        key_preview_title: str,
        open_button: str,
        copy_again_link: str,
        watch_label: str,
        manual_link: str,
        fallback_intro: str,
        fallback_confirm_button: str,
        rate_limit_note: str,
        key_text: str,
    ) -> None:
        super().__init__()
        self.title = title_manual
        self.body_manual = body_manual
        self.key_text = key_text
        self.key_preview_title = key_preview_title
        self.open_button = open_button
        self.copy_again_link = copy_again_link
        self.watch_label = watch_label
        self.rate_limit_note = rate_limit_note
        self.manual_link = manual_link
        self.fallback_intro = fallback_intro
        self.fallback_confirm_button = fallback_confirm_button

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_manual, id="body")
        yield KeyPreview(self.key_text, title=self.key_preview_title)
        yield PublishActions(
            open_url=GH_KEYS_URL,
            show_no_github=False,
            open_label=self.open_button,
            copy_label=self.copy_again_link,
        )
        yield WatcherRow(self.watch_label, rate_limit_text=self.rate_limit_note)
        yield LinkRow(self.manual_link, id="manual-link", classes="muted")

    def on_mount(self) -> None:
        self.app.copy_to_clipboard(self.key_text)
        self.app.open_url(GH_KEYS_URL)

    @on(PublishActions.Opened)
    def _opened(self, event: PublishActions.Opened) -> None:
        self.app.open_url(event.url)

    @on(PublishActions.CopyAgain)
    def _copy_again(self) -> None:
        self.app.copy_to_clipboard(self.key_text)

    @on(LinkRow.Pressed, "#manual-link")
    async def _show_fallback(self) -> None:
        if self.query("#fallback-panel"):
            return
        panel = FallbackPanel(
            key_text=self.key_text,
            target_url=GH_KEYS_URL,
            intro=self.fallback_intro,
            confirm_label=self.fallback_confirm_button,
        )
        await self.query_one(Card).mount(panel, before=self.query_one(WatcherRow))
        panel.visible = True
        self.query_one("#manual-link", LinkRow).display = False

    @on(FallbackPanel.Confirmed)
    def _fallback_confirmed(self) -> None:
        self.query_one("#fallback-panel", FallbackPanel).visible = False


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
            "manual_link": "Paste it manually →",
            "fallback_intro": (
                "Copy your signature below, then paste it at github.com/settings/keys."
            ),
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
        auto_keys = {"title_auto", "status_auto"}
        if caps.gh_authenticated:
            return GhAddAutoView(**{k: s[k] for k in auto_keys})
        return GhAddManualView(
            **{k: v for k, v in s.items() if k not in auto_keys},
            key_text=gs.selected.key.fingerprint if gs.selected and gs.selected.key else "",
        )
