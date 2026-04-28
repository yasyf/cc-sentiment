from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import screen as t
from textual.app import ComposeResult

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.ui import BaseState, Screen
from cc_sentiment.tui.onboarding.widgets import (
    KeyPreview,
    PublishActions,
    WatcherRow,
)
from cc_sentiment.tui.widgets.body import Body
from cc_sentiment.tui.widgets.card_screen import CardScreen


GIST_NEW_URL = "https://gist.github.com/new"


@dataclass(frozen=True)
class State(BaseState):
    pass


class PublishView(CardScreen[None]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    PublishView > Card { min-width: 60; max-width: 80; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        key_text: str,
        key_preview_title: str,
        show_no_github: bool,
        no_github_label: str,
        open_label: str,
        copy_label: str,
        watch_label: str,
        rate_limit_text: str,
        fallback_intro: str,
        fallback_confirm_label: str,
        resumed: bool,
    ) -> None:
        super().__init__()
        self.title = title
        self.body_text = body
        self.key_text = key_text
        self.key_preview_title = key_preview_title
        self.show_no_github = show_no_github
        self.no_github_label = no_github_label
        self.open_label = open_label
        self.copy_label = copy_label
        self.watch_label = watch_label
        self.rate_limit_text = rate_limit_text
        self.fallback_intro = fallback_intro
        self.fallback_confirm_label = fallback_confirm_label
        self.resumed = resumed

    def compose_card(self) -> ComposeResult:
        yield Body(self.body_text, id="body")
        yield KeyPreview(self.key_text, title=self.key_preview_title)
        yield PublishActions(
            open_url=GIST_NEW_URL,
            show_no_github=self.show_no_github,
            open_label=self.open_label,
            copy_label=self.copy_label,
            no_github_label=self.no_github_label,
        )
        yield WatcherRow(self.watch_label, rate_limit_text=self.rate_limit_text)

    def on_mount(self) -> None:
        if self.resumed:
            self.add_class("resumed")
            self.app.copy_to_clipboard(self.key_text)
            self.app.open_url(GIST_NEW_URL)


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

    def render(self, gs: GlobalState, caps: Capabilities) -> t.Screen:
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

        Path-dependent rendering — read inline:
          - The selected key (`gs.selected`) drives the preview block.
          - The username in the watcher / gist URL comes from
            `gs.identity.github_username`.
          - "I don't use GitHub →" is shown iff `caps.has_gpg` (so an
            email fallback exists).
          - When `gs.resumed_from_pending` is True, the screen mounts in
            resume mode: auto re-copy + auto re-open the browser, then
            silently watch (per plan Q&A: "Pending gist resume — Re-copy
            and reopen"). The user shouldn't notice they were resumed.

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

        Subtle hints:
          - No URL input, no Check Now, no elapsed/debug rows
            (per plan: forbidden UX terms).
          - On gist API rate-limit while polling, a tiny muted note
            appears under the watcher row: "GitHub busy — retrying."
            Polling continues (per plan Q&A: "Subtle warning while
            continuing").
          - No eager verify before a candidate gist exists (per plan).
        """
        s = self.strings()
        key_text = gs.selected.key.fingerprint if gs.selected and gs.selected.key else ""
        return PublishView(
            title=s["title"],
            body=s["body"],
            key_text=key_text,
            key_preview_title=s["key_preview_title"],
            show_no_github=caps.has_gpg,
            no_github_label=s["no_github_link"],
            open_label=s["open_button"],
            copy_label=s["copy_again_link"],
            watch_label=s["watch_label"],
            rate_limit_text=s["rate_limit_note"],
            fallback_intro=s["fallback_intro"],
            fallback_confirm_label=s["fallback_confirm_button"],
            resumed=gs.resumed_from_pending,
        )
