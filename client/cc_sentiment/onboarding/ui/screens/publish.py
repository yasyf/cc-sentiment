from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual import screen as t
from textual.app import ComposeResult

from cc_sentiment.onboarding import Capabilities, Stage, State as GlobalState
from cc_sentiment.onboarding.events import Event, TroubleChoseEmail
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


GIST_NEW_URL = "https://gist.github.com/new"


@dataclass(frozen=True)
class State(BaseState):
    pass


class PublishView(CardScreen[Event]):
    DEFAULT_CSS: ClassVar[str] = CardScreen.DEFAULT_CSS + """
    PublishView > Card { min-width: 60; max-width: 80; }
    PublishView LinkRow#manual-link { margin: 1 0 0 0; }
    """

    def __init__(
        self,
        *,
        title: str,
        body: str,
        key_text: str,
        key_preview_title: str,
        show_no_github: bool,
        no_github_link: str,
        open_button: str,
        copy_again_link: str,
        watch_label: str,
        rate_limit_note: str,
        manual_link: str,
        fallback_intro: str,
        fallback_confirm_button: str,
        resumed: bool,
    ) -> None:
        super().__init__()
        self.title = title
        self.body = body
        self.key_text = key_text
        self.key_preview_title = key_preview_title
        self.show_no_github = show_no_github
        self.no_github_link = no_github_link
        self.open_button = open_button
        self.copy_again_link = copy_again_link
        self.watch_label = watch_label
        self.rate_limit_note = rate_limit_note
        self.manual_link = manual_link
        self.fallback_intro = fallback_intro
        self.fallback_confirm_button = fallback_confirm_button
        self.resumed = resumed

    def compose_card(self) -> ComposeResult:
        yield Body(self.body, id="body")
        yield KeyPreview(self.key_text, title=self.key_preview_title)
        yield PublishActions(
            open_url=GIST_NEW_URL,
            show_no_github=self.show_no_github,
            open_label=self.open_button,
            copy_label=self.copy_again_link,
            no_github_label=self.no_github_link,
        )
        yield WatcherRow(self.watch_label, rate_limit_text=self.rate_limit_note)
        yield LinkRow(self.manual_link, id="manual-link", classes="muted")

    def on_mount(self) -> None:
        if self.resumed:
            self.add_class("resumed")
        self.app.copy_to_clipboard(self.key_text)
        self.app.open_url(GIST_NEW_URL)

    @on(PublishActions.Opened)
    def _opened(self, event: PublishActions.Opened) -> None:
        self.app.open_url(event.url)

    @on(PublishActions.CopyAgain)
    def _copy_again(self) -> None:
        self.app.copy_to_clipboard(self.key_text)

    @on(PublishActions.NoGithub)
    def _no_github(self) -> None:
        self.dismiss(TroubleChoseEmail())

    @on(LinkRow.Pressed, "#manual-link")
    async def _show_fallback(self) -> None:
        if self.query("#fallback-panel"):
            return
        panel = FallbackPanel(
            key_text=self.key_text,
            target_url=GIST_NEW_URL,
            intro=self.fallback_intro,
            confirm_label=self.fallback_confirm_button,
        )
        await self.query_one(Card).mount(panel, before=self.query_one(WatcherRow))
        panel.visible = True
        self.query_one("#manual-link", LinkRow).display = False

    @on(FallbackPanel.Confirmed)
    def _fallback_confirmed(self) -> None:
        self.query_one("#fallback-panel", FallbackPanel).visible = False


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
            "manual_link": "Paste it manually →",
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
        return PublishView(
            **self.strings(),
            key_text=gs.selected.key.fingerprint if gs.selected and gs.selected.key else "",
            show_no_github=caps.has_gpg,
            resumed=gs.resumed_from_pending,
        )
