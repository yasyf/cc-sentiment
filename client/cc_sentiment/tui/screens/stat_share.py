from __future__ import annotations

import contextlib
import webbrowser
from typing import ClassVar
from urllib.parse import urlencode

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, ContentSwitcher, Label

from cc_sentiment.models import GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.upload import Uploader

from cc_sentiment.tui.widgets import SpinnerLine


class StatShareScreen(ModalScreen[None]):
    DEFAULT_CSS = """
    #stat-box { width: 76; height: auto; border: heavy $accent; padding: 2 3; }
    #stat-box .title { text-style: bold; color: $text; margin: 0 0 1 0; }
    #stat-box .stat { color: $accent; text-style: bold; margin: 0 0 1 0; }
    #stat-box .detail { color: $text-muted; margin: 0 0 1 0; }
    #stat-box Button { margin: 1 1 0 0; }
    #stat-switch { height: auto; }
    #stat-loading, #stat-ready { height: auto; }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    TWEET_INTENT_URL: ClassVar[str] = "https://twitter.com/intent/tweet"
    POLL_INTERVAL_SECONDS: ClassVar[float] = 8.0

    stat: reactive[MyStat | None] = reactive(None)

    def __init__(self, config: SSHConfig | GPGConfig | GistConfig) -> None:
        super().__init__()
        self.config = config

    @property
    def contributor_id(self) -> str:
        return self.config.contributor_id

    @property
    def contributor_type(self) -> str:
        return self.config.contributor_type

    @property
    def share_url(self) -> str:
        from cc_sentiment.tui.app import CCSentimentApp
        assert self.stat is not None
        params = {"t": self.stat.text} | (
            {"u": self.contributor_id} if self.contributor_type in ("github", "gist") else {}
        )
        return f"{CCSentimentApp.DASHBOARD_URL}/?{urlencode(params)}"

    @property
    def tweet_url(self) -> str:
        assert self.stat is not None
        return f"{self.TWEET_INTENT_URL}?{urlencode({'text': self.stat.tweet_text, 'url': self.share_url})}"

    def compose(self) -> ComposeResult:
        with Vertical(id="stat-box"):
            with ContentSwitcher(initial="stat-loading", id="stat-switch"):
                with Vertical(id="stat-loading"):
                    yield SpinnerLine(id="stat-spinner")
                    yield Label("Generating your personalized card…", classes="detail")
                    with Horizontal():
                        yield Button("Close", id="stat-cancel", variant="default")
                with Vertical(id="stat-ready"):
                    yield Label("Your cc-sentiment snapshot", classes="title")
                    yield Label("", id="stat-text", classes="stat")
                    yield Label(
                        "Share it? The card on Twitter will show your GitHub avatar and this stat.",
                        classes="detail",
                    )
                    with Horizontal():
                        yield Button("Tweet it", id="stat-tweet", variant="primary")
                        yield Button("Not now", id="stat-skip", variant="default")

    def on_mount(self) -> None:
        self.query_one("#stat-spinner", SpinnerLine).spinner.text = "Talking to sentiments.cc"
        self._poll_for_stat()

    @work(exclusive=True, group="stat-poll")
    async def _poll_for_stat(self) -> None:
        uploader = Uploader()
        while self.stat is None:
            with contextlib.suppress(httpx.HTTPError, httpx.InvalidURL):
                self.stat = await uploader.fetch_my_stat(self.config)
            if self.stat is None:
                await anyio.sleep(self.POLL_INTERVAL_SECONDS)

    def watch_stat(self, stat: MyStat | None) -> None:
        if stat is None:
            return
        self.query_one("#stat-text", Label).update(f"You are {stat.text}.")
        self.query_one("#stat-switch", ContentSwitcher).current = "stat-ready"

    @on(Button.Pressed, "#stat-tweet")
    async def on_tweet_button(self) -> None:
        await self._open_tweet()

    @on(Button.Pressed, "#stat-skip")
    def on_skip_button(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed, "#stat-cancel")
    def on_cancel_button(self) -> None:
        self.dismiss(None)

    def action_skip(self) -> None:
        self.dismiss(None)

    async def _open_tweet(self) -> None:
        await anyio.to_thread.run_sync(webbrowser.open, self.tweet_url)
        self.dismiss(None)
