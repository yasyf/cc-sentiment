from __future__ import annotations

import contextlib
from dataclasses import replace

import anyio
import anyio.to_thread
import httpx
from textual.css.query import NoMatches

from cc_sentiment.daemon import LaunchAgent
from cc_sentiment.engines import EngineFactory
from cc_sentiment.engines.protocol import DEFAULT_MODEL
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.nlp import NLP
from cc_sentiment.pipeline import ScanCache
from cc_sentiment.repo import Repository

from cc_sentiment.tui.dashboard.popovers import BootingScreen
from cc_sentiment.tui.dashboard.progress import DebugState
from cc_sentiment.tui.dashboard.view import ProcessingView
from cc_sentiment.tui.dashboard.widgets import DebugSection

__all__ = ["DashboardLifecycle"]


class DashboardLifecycle:
    async def on_mount(self) -> None:
        self.view = ProcessingView(self)
        self._set_debug(nlp_state="loading")
        self.run_worker(self._load_nlp(), name="spacy-load", group="spacy-load", exclusive=True, exit_on_error=False)
        self._boot_screen = BootingScreen()
        await self.app.push_screen(self._boot_screen)
        self._boot_screen.status = "Loading your local data..."
        self.repo = await anyio.to_thread.run_sync(Repository.open, self.db_path)
        self.scan_cache = ScanCache(self.repo)
        await self._seed_from_repo()
        self.view.set_schedule_available(
            LaunchAgent.is_supported() and not LaunchAgent.is_installed()
        )
        self.set_interval(self.CTA_ROTATE_SECONDS, self.view.rotate_cta)
        self.set_interval(1.0, self._tick_progress_label)
        self.run_flow()

    def _tick_progress_label(self) -> None:
        if self.view is None:
            return
        if self._scoring.start_time > 0 and self.scored < self.total:
            self.view.update_progress_label(self._scoring, self.scored, self.total)

    async def _load_nlp(self) -> None:
        await NLP.ensure_ready()
        await Lexicon.ensure_ready()
        if NLP.failed:
            self._set_debug(nlp_state="failed", nlp_output=NLP.last_download_output)
            return
        self._set_debug(nlp_state="ready")

    def _maybe_prewarm(self) -> None:
        if self._prewarmed:
            return
        if EngineFactory.default() != "mlx":
            return
        self._prewarmed = True
        self.run_worker(self._prewarm_model(), name="prewarm-model", exit_on_error=False)

    async def _prewarm_model(self) -> None:
        EngineFactory.configure_hub_progress()
        from huggingface_hub import snapshot_download
        from huggingface_hub.utils import disable_progress_bars

        disable_progress_bars()
        self._set_debug(prewarm_model="running")
        try:
            await anyio.to_thread.run_sync(snapshot_download, DEFAULT_MODEL)
        except (OSError, httpx.HTTPError) as exc:
            self._set_debug(prewarm_model=f"failed: {exc.__class__.__name__}")
            return
        from cc_sentiment.sentiment import AdapterFuser, SentimentClassifier

        try:
            fused_dir = await anyio.to_thread.run_sync(AdapterFuser.ensure_fused, DEFAULT_MODEL)
        except OSError as exc:
            self._set_debug(prewarm_model=f"failed: {exc.__class__.__name__}")
            return
        try:
            classifier = SentimentClassifier(fused_dir)
            await classifier.ensure_loaded()
        except (OSError, RuntimeError) as exc:
            self._set_debug(prewarm_model=f"failed: {exc.__class__.__name__}")
            return
        self._prewarmed_classifier = classifier
        self._set_debug(prewarm_model="done")

    async def _dismiss_boot_screen(self) -> None:
        if self._boot_screen is None:
            return
        self._boot_screen.dismiss(None)
        self._boot_screen = None

    def _set_boot_status(self, value: str) -> None:
        if self._boot_screen is not None:
            self._boot_screen.status = value

    def _debug(self, msg: str) -> None:
        if not self.debug_mode:
            return
        if self._boot_screen is not None:
            self._boot_screen.append_detail(f"debug: {msg}")
            return
        if self.view is None:
            return
        with contextlib.suppress(NoMatches):
            self.view.append_status(f"[red dim]debug:[/] {msg}")

    def _set_debug(self, **fields: object) -> None:
        for name, value in fields.items():
            setattr(self._debug_state, name, value)
        self.debug_state = replace(self._debug_state)

    def watch_debug_state(self, value: DebugState | None) -> None:
        if value is None or not self.debug_mode:
            return
        try:
            section = self.query_one("#debug", DebugSection)
        except NoMatches:
            return
        section.render_state(value)

    async def on_unmount(self) -> None:
        if self.repo:
            await anyio.to_thread.run_sync(self.repo.close)

    async def _seed_from_repo(self) -> None:
        assert self.repo is not None
        assert self.view is not None
        existing = await anyio.to_thread.run_sync(self.repo.all_records)
        if not existing:
            return
        self.records = list(existing)
        _, _, total_files = await anyio.to_thread.run_sync(self.repo.stats)
        self.view.show_total_files(total_files)
        self.view.render_scores(self.records)
