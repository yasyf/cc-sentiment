from __future__ import annotations

import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

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
from cc_sentiment.tui.view import ShareState

PREPARING_LABEL = "Preparing share…"
TWEET_LABEL = "Tweet it"
MINT_FAILED_LABEL = "Share unavailable"


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
    StatShareScreen { background: $background 60%; }
    StatShareScreen > #dialog-box .stat { width: 100%; color: $accent; text-style: bold; margin: 0 0 1 0; }
    StatShareScreen > #dialog-box .detail { width: 100%; }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(self, config: SSHConfig | GPGConfig | GistConfig, stat: MyStat) -> None:
        super().__init__()
        self.config = config
        self.stat = stat

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label(ShareState.TITLE, classes="title")
            yield Static(ShareState.stat_line(self.stat), classes="stat")
            yield Static(ShareState.DETAIL, classes="detail")
            with Horizontal():
                yield Button(PREPARING_LABEL, id="stat-tweet", variant="primary", disabled=True)
                yield Button("Not now", id="stat-skip", variant="default")

    def on_mount(self) -> None:
        self.share_id: str | None = None
        self.app.run_worker(
            self._mint_share(),
            name="card-mint-screen", exit_on_error=False,
        )

    async def _mint_share(self) -> None:
        try:
            response = await Uploader().mint_share(self.config)
        except (httpx.HTTPError, httpx.InvalidURL):
            self.query_one("#stat-tweet", Button).label = MINT_FAILED_LABEL
            return
        self.share_id = response.id
        tweet_button = self.query_one("#stat-tweet", Button)
        tweet_button.label = TWEET_LABEL
        tweet_button.disabled = False

    @on(Button.Pressed, "#stat-tweet")
    async def on_tweet_button(self) -> None:
        if self.share_id is None:
            return
        await anyio.to_thread.run_sync(
            webbrowser.open, Uploader.tweet_url(self.share_id, self.stat.tweet_text)
        )
        self.dismiss(None)

    @on(Button.Pressed, "#stat-skip")
    def on_skip_button(self) -> None:
        self.dismiss(None)

    def action_skip(self) -> None:
        self.dismiss(None)
