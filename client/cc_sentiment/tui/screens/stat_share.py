from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import urlencode

import anyio
import anyio.to_thread
import httpx
from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Label, Static

from cc_sentiment.models import GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.upload import Uploader

from cc_sentiment.tui.screens.dialog import Dialog


@dataclass
class CardPoller:
    config: SSHConfig | GPGConfig | GistConfig
    on_ready: Callable[[MyStat], None]
    on_state: Callable[[int, str, float, str | None], None] = lambda *_: None
    POLL_INTERVAL_SECONDS: ClassVar[float] = 4.0
    POLL_BACKOFF_AFTER: ClassVar[int] = 5
    POLL_BACKOFF_SECONDS: ClassVar[float] = 15.0
    MAX_POLL_SECONDS: ClassVar[float] = 180.0

    attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    cancelled: bool = False

    def cancel(self, reason: str = "dismissed") -> None:
        self.cancelled = True
        self.on_state(self.attempts, self.last_status_from(reason), self.elapsed(), reason)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    @staticmethod
    def last_status_from(reason: str) -> str:
        match reason:
            case "ready":
                return "http 200"
            case "timeout":
                return "timeout"
            case "dismissed":
                return "dismissed"
            case _:
                return reason

    async def run(self) -> None:
        uploader = Uploader()
        while not self.cancelled and self.elapsed() < self.MAX_POLL_SECONDS:
            self.attempts += 1
            status = "polling"
            self.on_state(self.attempts, status, self.elapsed(), None)
            stat: MyStat | None = None
            try:
                stat = await uploader.fetch_my_stat(self.config)
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                status = f"error: {exc.__class__.__name__}"
            else:
                status = "http 200" if stat is not None else "http 404"
            self.on_state(self.attempts, status, self.elapsed(), None)
            if stat is not None:
                self.on_state(self.attempts, "http 200", self.elapsed(), "ready")
                self.on_ready(stat)
                return
            delay = (
                self.POLL_BACKOFF_SECONDS
                if self.attempts >= self.POLL_BACKOFF_AFTER
                else self.POLL_INTERVAL_SECONDS
            )
            await anyio.sleep(delay)
        if not self.cancelled:
            self.on_state(self.attempts, "timeout", self.elapsed(), "timeout")


class StatShareScreen(Dialog[None]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    StatShareScreen > #dialog-box .stat { width: 100%; color: $accent; text-style: bold; margin: 0 0 1 0; }
    StatShareScreen > #dialog-box .detail { width: 100%; }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    TWEET_INTENT_URL: ClassVar[str] = "https://twitter.com/intent/tweet"

    def __init__(self, config: SSHConfig | GPGConfig | GistConfig, stat: MyStat) -> None:
        super().__init__()
        self.config = config
        self.stat = stat

    @property
    def contributor_id(self) -> str:
        return self.config.contributor_id

    @property
    def contributor_type(self) -> str:
        return self.config.contributor_type

    @property
    def share_url(self) -> str:
        return Uploader.share_url(self.config, self.stat)

    @property
    def tweet_url(self) -> str:
        return f"{self.TWEET_INTENT_URL}?{urlencode({'text': self.stat.tweet_text, 'url': self.share_url})}"

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label("Your cc-sentiment snapshot", classes="title")
            yield Static(f"You are {self.stat.text}.", classes="stat")
            yield Static(
                "Share it? The card on Twitter will show your GitHub avatar and this stat.",
                classes="detail",
            )
            with Horizontal():
                yield Button("Tweet it", id="stat-tweet", variant="primary")
                yield Button("Not now", id="stat-skip", variant="default")

    def on_mount(self) -> None:
        self.app.run_worker(
            Uploader().prewarm_share_card(self.config, self.stat),
            name="card-prewarm-screen", exit_on_error=False,
        )

    @on(Button.Pressed, "#stat-tweet")
    async def on_tweet_button(self) -> None:
        await anyio.to_thread.run_sync(webbrowser.open, self.tweet_url)
        self.dismiss(None)

    @on(Button.Pressed, "#stat-skip")
    def on_skip_button(self) -> None:
        self.dismiss(None)

    def action_skip(self) -> None:
        self.dismiss(None)
