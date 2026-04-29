from __future__ import annotations

import asyncio
import contextlib
import subprocess

import anyio
import anyio.to_thread
import httpx
from textual import work
from textual.css.query import NoMatches
from textual.widgets import Static

from cc_sentiment.engines import (
    ClaudeCLIEngine,
    FrustrationFilter,
    ImperativeMildIrritationFilter,
    InferenceEngine,
)
from cc_sentiment.hardware import Hardware
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.models import SentimentRecord
from cc_sentiment.nlp import NLP
from cc_sentiment.pipeline import Pipeline
from cc_sentiment.transcripts import TranscriptParser
from cc_sentiment.upload import (
    UPLOAD_POOL_TIMEOUT_SECONDS,
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    UploadPool,
    UploadProgress,
    Uploader,
)

from cc_sentiment.tui.dashboard.format import TimeFormat
from cc_sentiment.tui.dashboard.moments_view import MomentsView
from cc_sentiment.tui.dashboard.stages import (
    Authenticating,
    Discovering,
    Error,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    Scoring,
    Uploading,
)
from cc_sentiment.tui.onboarding.runner import OnboardingScreen

__all__ = ["DashboardFlow"]


class DashboardFlow:
    async def _authenticate(self) -> bool:
        while True:
            if self.state.config is None:
                ok = await self.app.push_screen_wait(OnboardingScreen(self.state))
                if not ok:
                    return False
                continue
            self.stage = Authenticating()
            self._set_boot_status("Connecting to sentiments.cc...")
            match await Uploader().probe_credentials(self.state.config):
                case AuthOk():
                    return True
                case AuthUnauthorized():
                    self._update_status(
                        "[$warning]sentiments.cc couldn't verify this key. Let's redo setup.[/]"
                    )
                    self.state.config = None
                    await anyio.to_thread.run_sync(self.state.save)
                    continue
                case AuthUnreachable(detail=d):
                    self._debug(f"AuthUnreachable: {d}")
                    self.stage = Error(f"[$error]Couldn't reach sentiments.cc.[/] [dim]{d}[/]")
                    return False
                case AuthServerError(status=s):
                    self._debug(f"AuthServerError: status={s}")
                    self.stage = Error(f"[$error]sentiments.cc had an error checking verification (HTTP {s}).[/]")
                    return False

    @work()
    async def run_flow(self) -> None:
        assert self.repo is not None
        assert self.view is not None
        assert self.engine is not None
        engine = self.engine
        self._debug(f"transcript-backend: {TranscriptParser.backend_name()}")

        classifier: InferenceEngine | None = None
        self.stage = Discovering()
        self._set_boot_status("Discovering transcripts...")
        assert self.scan_cache is not None
        scan = await self.scan_cache.get()
        pending = await anyio.to_thread.run_sync(self.repo.pending_records)
        self._debug(f"transcripts={len(scan.transcripts)} pending={len(pending)}")

        if (scan.transcripts or pending) and not await self._authenticate():
            await self._dismiss_boot_screen()
            self.app.exit()
            return

        bucket_count = scan.total_new_buckets
        if scan.transcripts:
            self._set_boot_status("Sizing things up...")
            self._debug(f"bucket_count={bucket_count}")
            rate = Hardware.estimate_buckets_per_sec(engine)
            if rate and rate > 0:
                self._update_status(
                    f"[dim]Found [b]{bucket_count:,}[/] moments. "
                    f"About {TimeFormat.format_duration(bucket_count / rate)} to score on this device.[/]"
                )
            else:
                self._update_status(f"[dim]Found [b]{bucket_count:,}[/] moments.[/]")

        pre_seed = await anyio.to_thread.run_sync(self.repo.pending_records)
        has_work = (scan.transcripts and bucket_count > 0) or bool(pre_seed)
        self._upload.reset()
        self._upload.preseed_count = len(pre_seed)

        if scan.transcripts and bucket_count > 0:
            if engine == "mlx":
                if self._model_cache.classifier is None:
                    self._set_boot_status("Loading the local model...")
                try:
                    inner = await self._model_cache.get()
                except (TimeoutError, OSError, RuntimeError) as exc:
                    await self._dismiss_boot_screen()
                    self.stage = Error(f"[$error]Couldn't start the local model.[/] [dim]{exc}[/]")
                    return
            else:
                inner = ClaudeCLIEngine(self.model_repo or ClaudeCLIEngine.HAIKU_MODEL)
            classifier = ImperativeMildIrritationFilter(FrustrationFilter(inner))

        await self._dismiss_boot_screen()

        if has_work:
            pool = UploadPool(
                uploader=Uploader(),
                state=self.state,
                repo=self.repo,
                progress=self._upload,
                on_progress_change=self._on_upload_progress_change,
                debug=self._debug,
            )

            async def producer() -> None:
                if pre_seed:
                    pool.queue_records(pre_seed)
                if scan.transcripts and bucket_count > 0:
                    assert classifier is not None
                    _, _, existing_files = await anyio.to_thread.run_sync(self.repo.stats)
                    self._begin_scoring(bucket_count, engine, existing_files + len(scan.transcripts))
                    moments = MomentsView(
                        app=self.app,
                        section=self.query_one("#moments-section"),
                        log=self.query_one("#moments-log", Static),
                    )
                    await NLP.ensure_ready()
                    await Lexicon.ensure_ready()
                    moments.show()
                    try:
                        await Pipeline.run(
                            self.repo, scan,
                            classifier=classifier,
                            on_records=self._add_records, on_bucket=self._add_buckets,
                            on_snippet=moments.add_snippet,
                            on_transcript_complete=pool.queue_records,
                            on_frustration=self._track_frustration,
                        )
                    finally:
                        self.stage = Uploading()

            try:
                await pool.run(producer)
            except TimeoutError:
                self._debug(f"upload: pool timed out after {UPLOAD_POOL_TIMEOUT_SECONDS}s")
                self.stage = Error(
                    f"[red bold]Uploads timed out after {UPLOAD_POOL_TIMEOUT_SECONDS // 60} min.[/] "
                    "Records kept locally — press R to retry once you're back online."
                )
                return

        match self._upload.fatal:
            case httpx.HTTPStatusError() as e if e.response.status_code in (401, 403):
                self.stage = Error(
                    f"[red bold]Server rejected upload ({e.response.status_code}).[/] "
                    "Run [b]cc-sentiment setup[/] again."
                )
                return
            case httpx.HTTPStatusError() as e:
                self.stage = Error(
                    f"[red bold]Server rejected upload ({e.response.status_code}).[/] "
                    f"Records kept locally — press R to retry."
                )
                return
            case subprocess.CalledProcessError() as e:
                self.stage = Error(
                    f"[red bold]Signing failed ({e.returncode}).[/] "
                    "Check that your signing key is still accessible, or run "
                    "[b]cc-sentiment[/] again to pick a different one."
                )
                return

        if self._upload.failed_batches > 0:
            self.stage = Error(
                f"[red bold]Couldn't upload {self._upload.failed_batches} "
                f"batch{'es' if self._upload.failed_batches != 1 else ''}.[/] "
                "Records kept locally — press R to retry once you're back online."
            )
            return

        uploaded = self._upload.uploaded_records > 0
        await self._enter_idle(uploaded=uploaded)

        if uploaded:
            assert self.state.config is not None
            is_first = not self.state.has_celebrated_first_upload
            if is_first:
                self.state.has_celebrated_first_upload = True
                await anyio.to_thread.run_sync(self.state.save)
                self.run_worker(self._auto_open_dashboard(), name="auto-open-dashboard", group="auto-open-dashboard", exclusive=True, exit_on_error=False)
            self.run_worker(self._fetch_card(self.state.config, push_share=is_first), name="card-fetch", group="card-fetch", exclusive=True, exit_on_error=True)

    def _on_upload_progress_change(self, progress: UploadProgress) -> None:
        if self.view is None:
            return
        with contextlib.suppress(NoMatches):
            self.view.update_upload(progress)

    async def _enter_idle(self, uploaded: bool) -> None:
        assert self.repo is not None
        total_buckets, total_sessions, total_files = await anyio.to_thread.run_sync(
            self.repo.stats
        )
        if uploaded:
            self.stage = IdleAfterUpload(
                total_buckets=total_buckets,
                total_sessions=total_sessions,
                total_files=total_files,
            )
        elif total_sessions == 0:
            self.stage = IdleEmpty()
        else:
            self.stage = IdleCaughtUp(
                total_buckets=total_buckets,
                total_sessions=total_sessions,
                total_files=total_files,
            )

    def _begin_scoring(self, total: int, engine: str, total_files: int) -> None:
        assert self.view is not None
        self.total = total
        self.scored = 0
        self._scoring.begin(Hardware.estimate_buckets_per_sec(engine), total)
        self.view.begin_scoring(total, total_files)
        self.view.update_progress_label(self._scoring, self.scored, self.total)
        self.stage = Scoring(total=total, engine=engine)

    def _add_buckets(self, n: int) -> None:
        assert self.view is not None
        asyncio.get_running_loop()
        self.scored += n
        self.view.bump_scored(self.scored, self._scoring)

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
        assert self.view is not None
        asyncio.get_running_loop()
        self.records.extend(new_records)
        self.view.render_scores(self.records)

    def _track_frustration(self, words: list[str]) -> None:
        assert self.view is not None
        asyncio.get_running_loop()
        self.view.track_frustration(words)
