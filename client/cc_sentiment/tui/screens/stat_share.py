from __future__ import annotations

import subprocess
import time
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from pydantic import ValidationError
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Static

from cc_sentiment.models import GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.upload import Uploader

from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.view import CtaState
from cc_sentiment.tui.widgets import ButtonRow

PREPARING_LABEL = "Preparing share…"
MINT_FAILED_LABEL = "Share unavailable"


@dataclass
class CardPoller:
    config: SSHConfig | GPGConfig | GistConfig
    on_ready: Callable[[MyStat], None]
    on_state: Callable[[int, str, float, str | None, float | None], None] = lambda *_: None
    POLL_INTERVAL_SECONDS: ClassVar[float] = 4.0
    POLL_BACKOFF_AFTER: ClassVar[int] = 5
    POLL_BACKOFF_SECONDS: ClassVar[float] = 15.0
    MAX_POLL_SECONDS: ClassVar[float] = 180.0
    TICK_SECONDS: ClassVar[float] = 1.0
    MAX_ERROR_DETAIL: ClassVar[int] = 80

    attempts: int = 0
    started_at: float = field(default_factory=time.monotonic)
    cancelled: bool = False
    last_status: str = "polling"
    next_retry_at: float | None = None

    def cancel(self, reason: str = "dismissed") -> None:
        self.cancelled = True
        self.last_status = self.last_status_from(reason)
        self.next_retry_at = None
        self._emit(reason)

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at

    def _emit(self, stopped: str | None) -> None:
        self.on_state(self.attempts, self.last_status, self.elapsed(), stopped, self.next_retry_at)

    @classmethod
    def truncate(cls, text: str) -> str:
        return text[: cls.MAX_ERROR_DETAIL - 1] + "…" if len(text) > cls.MAX_ERROR_DETAIL else text

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
        async with anyio.create_task_group() as tg:
            tg.start_soon(self._tick_loop)
            await self._poll_loop()
            tg.cancel_scope.cancel()

    async def _tick_loop(self) -> None:
        while not self.cancelled:
            await anyio.sleep(self.TICK_SECONDS)
            self._emit(None)

    async def _poll_loop(self) -> None:
        uploader = Uploader()
        while not self.cancelled and self.elapsed() < self.MAX_POLL_SECONDS:
            self.attempts += 1
            self.last_status = "polling"
            self.next_retry_at = None
            self._emit(None)
            stat: MyStat | None = None
            try:
                stat = await uploader.fetch_my_stat(self.config)
            except (httpx.HTTPError, httpx.InvalidURL) as exc:
                self.last_status = f"error: {self.truncate(f'{exc.__class__.__name__}: {exc}'.strip())}"
            else:
                self.last_status = "http 200" if stat is not None else "http 404"
            if stat is not None:
                self._emit("ready")
                self.on_ready(stat)
                return
            delay = (
                self.POLL_BACKOFF_SECONDS
                if self.attempts >= self.POLL_BACKOFF_AFTER
                else self.POLL_INTERVAL_SECONDS
            )
            self.next_retry_at = self.elapsed() + delay
            self._emit(None)
            await anyio.sleep(delay)
        if not self.cancelled:
            self.last_status = "timeout"
            self.next_retry_at = None
            self._emit("timeout")


class StatShareScreen(Dialog[None]):
    DEFAULT_CSS = Dialog.DEFAULT_CSS + """
    StatShareScreen { background: $background 60%; }
    StatShareScreen > #dialog-box .title { text-align: center; }
    StatShareScreen > #dialog-box .stat {
        width: 100%; color: $accent; text-style: bold;
        text-align: center; margin: 0 0 1 0;
    }
    StatShareScreen > #dialog-box .detail { width: 100%; text-align: center; }
    StatShareScreen > #dialog-box ButtonRow { align-horizontal: center; }
    """

    BINDINGS = [("escape", "skip", "Skip")]

    def __init__(
        self,
        config: SSHConfig | GPGConfig | GistConfig,
        stat: MyStat,
        on_share_state: Callable[[str], None] = lambda _: None,
    ) -> None:
        super().__init__()
        self.config = config
        self.stat = stat
        self.on_share_state = on_share_state

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog-box"):
            yield Label(CtaState.SNAPSHOT_TITLE, classes="title")
            yield Static(CtaState.tweet_title(self.stat), classes="stat")
            yield Static(CtaState.TWEET_DETAIL, classes="detail")
            yield ButtonRow(
                Button(PREPARING_LABEL, id="stat-tweet", variant="primary", disabled=True),
                Button("Not now", id="stat-skip", variant="default"),
            )

    def on_mount(self) -> None:
        self.share_id: str | None = None
        self.app.run_worker(
            self._mint_share(),
            name="card-mint-screen", exit_on_error=False,
        )

    async def _mint_share(self) -> None:
        self.on_share_state("minting")
        try:
            response = await Uploader().mint_share(self.config)
        except (
            httpx.HTTPError,
            httpx.InvalidURL,
            subprocess.CalledProcessError,
            subprocess.TimeoutExpired,
            OSError,
            AssertionError,
            ValidationError,
            TimeoutError,
        ) as exc:
            self.on_share_state(f"failed: {exc.__class__.__name__}")
            self.query_one("#stat-tweet", Button).label = MINT_FAILED_LABEL
            return
        self.on_share_state("ready")
        self.share_id = response.id
        tweet_button = self.query_one("#stat-tweet", Button)
        tweet_button.label = CtaState.TWEET_LABEL
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
