from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from pydantic import ValidationError
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Button, Label, Static

from cc_sentiment.models import GistGPGConfig, GistConfig, GPGConfig, MyStat, SSHConfig
from cc_sentiment.upload import Uploader

from cc_sentiment.tui.screens.dialog import Dialog
from cc_sentiment.tui.system import Browser
from cc_sentiment.tui.view import CtaState
from cc_sentiment.tui.widgets import ButtonRow

PREPARING_LABEL = "Preparing share…"
MINT_FAILED_LABEL = "Share unavailable"


@dataclass
class CardFetcher:
    config: SSHConfig | GPGConfig | GistConfig | GistGPGConfig
    on_ready: Callable[[MyStat], None]
    on_state: Callable[[str, float, str | None], None] = lambda *_: None
    MAX_ERROR_DETAIL: ClassVar[int] = 80

    @classmethod
    def truncate(cls, text: str) -> str:
        return text[: cls.MAX_ERROR_DETAIL - 1] + "…" if len(text) > cls.MAX_ERROR_DETAIL else text

    async def run(self) -> None:
        started = time.monotonic()
        self.on_state("fetching", 0.0, None)
        try:
            stat = await Uploader().fetch_my_stat(self.config)
        except (httpx.HTTPError, ValidationError) as exc:
            self.on_state(
                f"error: {self.truncate(f'{exc.__class__.__name__}: {exc}'.strip())}",
                time.monotonic() - started,
                "error",
            )
            return
        if stat is not None:
            self.on_state("http 200", time.monotonic() - started, "ready")
            self.on_ready(stat)
            return
        self.on_state("http 404", time.monotonic() - started, "no card")


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
        config: SSHConfig | GPGConfig | GistConfig | GistGPGConfig,
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
            Browser.open, Uploader.tweet_url(self.share_id, self.stat.tweet_text)
        )
        self.dismiss(None)

    @on(Button.Pressed, "#stat-skip")
    def on_skip_button(self) -> None:
        self.dismiss(None)

    def action_skip(self) -> None:
        self.dismiss(None)
