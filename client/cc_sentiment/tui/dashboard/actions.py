from __future__ import annotations

import subprocess

import anyio
import anyio.to_thread

from cc_sentiment.daemon import LaunchAgent
from cc_sentiment.models import MyStat
from cc_sentiment.upload import DASHBOARD_URL, Config

from cc_sentiment.tui.dashboard.popovers import StatShareScreen
from cc_sentiment.tui.dashboard.popovers.stat_share import CardFetcher
from cc_sentiment.tui.dashboard.stages import (
    Error,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
)

__all__ = ["DashboardActions"]


class DashboardActions:
    async def _fetch_card(self, config: Config, push_share: bool) -> None:
        self._set_debug(share_state="waiting on stat")
        await CardFetcher(
            config=config,
            on_ready=lambda stat: self._on_card_ready(stat, push_share=push_share),
            on_state=self._on_card_state,
        ).run()
        if isinstance(self.stage, IdleAfterUpload) and self._debug_state.card_stopped != "ready":
            self._update_status(self._uploaded_status_text())

    def _on_card_ready(self, stat: MyStat, push_share: bool) -> None:
        if self.state.config is None or self.view is None:
            return
        self.view.set_tweet(self.state.config, stat)
        if push_share:
            self.app.push_screen(StatShareScreen(
                self.state.config, stat,
                on_share_state=lambda s: self._set_debug(share_state=s),
            ))
        if isinstance(self.stage, IdleAfterUpload):
            self._update_status(self._uploaded_status_text())

    async def _handle_cta_action(self) -> None:
        assert self.view is not None
        cta = self.view.cta
        match cta.showing:
            case "tweet":
                assert cta.tweet_config is not None and cta.tweet_stat is not None
                self.app.push_screen(StatShareScreen(cta.tweet_config, cta.tweet_stat))
            case "schedule":
                await self._install_daemon()

    def _on_card_state(self, status: str, elapsed: float, stopped: str | None) -> None:
        self._set_debug(
            card_last_status=status,
            card_elapsed=elapsed,
            card_stopped=stopped,
        )
        if isinstance(self.stage, IdleAfterUpload):
            self._update_status(self._uploaded_status_text())

    async def _install_daemon(self) -> None:
        assert self.view is not None
        if not LaunchAgent.is_supported():
            self._update_status(
                "[$warning]Background scheduling is only available on macOS.[/] "
                "[dim]Use cron or your platform's scheduler to run `cc-sentiment run` daily.[/]"
            )
            return
        try:
            await anyio.to_thread.run_sync(LaunchAgent.install)
        except subprocess.CalledProcessError as e:
            self._update_status(
                f"[$warning]Couldn't schedule the background job ({e.returncode}).[/] "
                "[dim]Try `cc-sentiment install` manually.[/]"
            )
            return
        self.view.set_schedule_available(False)
        self._update_status(
            "[$success]Scheduled.[/] cc-sentiment will run daily. "
            "[dim]Undo with `cc-sentiment uninstall`.[/]"
        )

    async def action_open_dashboard(self) -> None:
        self.app.open_url(DASHBOARD_URL)
        self._update_status(f"[dim]Opened aggregate stats: {DASHBOARD_URL}.[/]")
        self.set_timer(3.0, lambda: self.watch_stage(self.stage))

    async def _auto_open_dashboard(self) -> None:
        await anyio.sleep(self.AUTO_OPEN_DASHBOARD_DELAY_SECONDS)
        self.app.open_url(DASHBOARD_URL)

    async def action_quit(self) -> None:
        self.workers.cancel_all()
        self.app.exit()

    async def action_rescan(self) -> None:
        match self.stage:
            case RescanConfirm():
                await self._reset_for_rescan()
                self.run_flow()
            case IdleEmpty() | IdleCaughtUp() | IdleAfterUpload() | Error() as prev:
                self.stage = RescanConfirm(prev=prev)
                self.set_timer(self.RESCAN_CONFIRM_SECONDS, self._cancel_rescan)

    async def _cancel_rescan(self) -> None:
        match self.stage:
            case RescanConfirm(prev=p):
                self.stage = p

    async def _reset_for_rescan(self) -> None:
        assert self.repo is not None
        assert self.view is not None
        await anyio.to_thread.run_sync(self.repo.clear_all)
        if self.scan_cache is not None:
            self.scan_cache.invalidate()
        self.records = []
        self.scored = 0
        self.total = 0
        self._scoring.reset()
        self._upload.reset()
        self._debug_state.reset()
        self.view.reset()
