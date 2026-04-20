from __future__ import annotations

import asyncio
import contextlib
import subprocess
import webbrowser
from dataclasses import replace
from pathlib import Path
from typing import ClassVar

import anyio
import anyio.to_thread
import httpx
from textual import on, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.reactive import reactive
from textual.widgets import (
    Button,
    Digits,
    Footer,
    Header,
    Label,
    Static,
)

from cc_sentiment.daemon import LaunchAgent
from cc_sentiment.engines import (
    OMLX_UVX_SPEC,
    ClaudeCLIEngine,
    EngineFactory,
    InferenceEngine,
)
from cc_sentiment.engines.protocol import DEFAULT_MODEL
from cc_sentiment.hardware import Hardware
from cc_sentiment.models import (
    CLIENT_VERSION,
    AppState,
    GistConfig,
    GPGConfig,
    MyStat,
    SentimentRecord,
    SSHConfig,
)
from cc_sentiment.lexicon import Lexicon
from cc_sentiment.nlp import NLP
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import TranscriptParser
from cc_sentiment.upload import (
    DASHBOARD_URL,
    UPLOAD_POOL_TIMEOUT_SECONDS,
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    UploadPool,
    UploadProgress,
    Uploader,
)

from cc_sentiment.tui.moments_view import MomentsView
from cc_sentiment.tui.format import TimeFormat
from cc_sentiment.tui.progress import DebugState, ScoringProgress
from cc_sentiment.tui.screens import (
    BootingScreen,
    CostReviewScreen,
    PlatformErrorScreen,
    SetupScreen,
    StatShareScreen,
)
from cc_sentiment.tui.screens.stat_share import CardPoller
from cc_sentiment.tui.stages import (
    Authenticating,
    Booting,
    Discovering,
    Error,
    IdleAfterUpload,
    IdleCaughtUp,
    IdleEmpty,
    RescanConfirm,
    Scoring,
    Stage,
    Uploading,
)
from cc_sentiment.tui.view import ProcessingView
from cc_sentiment.tui.widgets import (
    Card,
    DebugSection,
    HourlyChart,
    ProgressRow,
    ScoreBar,
)


class CCSentimentApp(App[None]):
    RESCAN_CONFIRM_SECONDS: ClassVar[float] = 5.0
    CTA_ROTATE_SECONDS: ClassVar[float] = 10.0

    CSS = """
    Screen { layout: vertical; background: $surface; }
    Dialog { background: $surface; }
    StatShareScreen { background: $background 60%; }
    #main { height: 1fr; padding: 1 2; }
    #header-section { height: auto; }
    #title-row { height: 3; }
    #title-text { width: 1fr; }
    #score-digits { width: auto; min-width: 20; color: $accent; }
    #score-label { text-align: right; height: 1; color: $text-muted; }
    #score-digits.inactive, #score-label.inactive { display: none; }
    .row { height: auto; }
    .row > Card { margin-right: 1; }
    .row > Card:last-of-type { margin-right: 0; }
    #sentiment-section { width: 2fr; }
    #hourly-section { width: 1fr; min-width: 32; }
    #moments-section { width: 2fr; }
    #stats-section { width: 1fr; min-width: 36; }
    #cta-section { width: 1fr; min-width: 36; }
    #cta-title { color: $accent; text-style: bold; margin: 0 0 1 0; }
    #cta-detail { color: $text-muted; margin: 0 0 1 0; }
    #cta-buttons { height: auto; }
    ProgressBar Bar > .bar--bar { color: $accent; }
    ProgressBar Bar > .bar--complete { color: $accent; }
    #hourly-chart { height: 7; }
    #moments-log { height: auto; min-height: 4; max-height: 10; }
    #stats-rows { height: auto; }
    #status-line { height: auto; margin: 1 0 0 0; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("r", "rescan", "Rescan"),
        ("o", "open_dashboard", "Open dashboard"),
    ]

    scored: reactive[int] = reactive(0)
    total: reactive[int] = reactive(0)
    uploaded_count: reactive[int] = reactive(0)
    status_text: reactive[str] = reactive("Initializing...")
    stage: reactive[Stage] = reactive(Booting())
    debug_state: reactive[DebugState | None] = reactive(None)

    def __init__(
        self,
        state: AppState,
        model_repo: str | None = None,
        db_path: Path | None = None,
        setup_only: bool = False,
        debug: bool = False,
    ) -> None:
        super().__init__()
        self.theme = "tokyo-night"
        self.state = state
        self.model_repo = model_repo
        self.db_path = db_path or Repository.default_path()
        self.setup_only = setup_only
        self.debug_mode = debug
        self.repo: Repository | None = None
        self.records: list[SentimentRecord] = []
        self.view = ProcessingView(self)
        self._scoring = ScoringProgress()
        self._upload = UploadProgress()
        self._debug_state = DebugState()
        self._boot_screen: BootingScreen | None = None
        self._prewarmed = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="main"):
            with Vertical(id="header-section"):
                with Horizontal(id="title-row"):
                    yield Static(f"[b]cc-sentiment[/b] [dim]v{CLIENT_VERSION}[/]", id="title-text")
                    yield Digits("-.--", id="score-digits", classes="inactive")
                yield Static("[dim]average sentiment[/]", id="score-label", classes="inactive")

            with Card(id="progress-section", title="progress", classes="inactive"):
                yield ProgressRow(
                    label="scoring",
                    bar_id="scan-progress",
                    context_id="progress-label",
                    id="scoring-row",
                    classes="inactive",
                )
                yield ProgressRow(
                    label="uploading",
                    bar_id="upload-progress",
                    context_id="upload-label",
                    id="upload-row",
                    classes="inactive",
                )

            with Horizontal(classes="row"):
                with Card(id="sentiment-section", title="sentiment", classes="inactive"):
                    for s in range(5, 0, -1):
                        bar = ScoreBar(s)
                        bar.id = f"bar-{s}"
                        self.view.register_score_bar(s, bar)
                        yield bar
                with Card(id="hourly-section", title="through the day", classes="inactive"):
                    yield HourlyChart(id="hourly-chart")

            with Horizontal(classes="row"):
                with Card(id="moments-section", title="moments", classes="inactive"):
                    yield Static("", id="moments-log")
                with Card(id="stats-section", title="stats", classes="inactive"):
                    yield Static("", id="stats-rows")
                with Card(id="cta-section", title="next", classes="inactive"):
                    yield Static("", id="cta-title")
                    yield Static("", id="cta-detail")
                    with Horizontal(id="cta-buttons"):
                        yield Button("", id="cta-action", variant="primary")

            if self.debug_mode:
                yield DebugSection(id="debug")

            yield Label("", id="status-line")
        yield Footer()

    async def on_mount(self) -> None:
        self.title = "cc-sentiment"
        self._set_debug(nlp_state="loading")
        self.run_worker(self._load_nlp(), name="spacy-load", exclusive=True, exit_on_error=False)
        self._boot_screen = BootingScreen()
        await self.push_screen(self._boot_screen)
        self._boot_screen.status = "Loading local cache..."
        self.repo = await anyio.to_thread.run_sync(Repository.open, self.db_path)
        await self._seed_from_repo()
        self.view.set_schedule_available(not LaunchAgent.is_installed())
        self.set_interval(self.CTA_ROTATE_SECONDS, self.view.rotate_cta)
        if self.setup_only:
            await self._dismiss_boot_screen()
            await self.push_screen_wait(SetupScreen(self.state))
            self.exit()
            return
        self.run_flow()

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
        if EngineFactory.default() != "omlx":
            return
        self._prewarmed = True
        self.run_worker(self._prewarm_uvx(), name="prewarm-uvx", exit_on_error=False)
        self.run_worker(self._prewarm_model(), name="prewarm-model", exit_on_error=False)

    async def _prewarm_uvx(self) -> None:
        self._set_debug(prewarm_uvx="running")
        try:
            proc = await asyncio.create_subprocess_exec(
                "uvx", "--from", OMLX_UVX_SPEC, "omlx", "--help",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
        except OSError as exc:
            self._set_debug(prewarm_uvx=f"failed: {exc.__class__.__name__}")
            return
        self._set_debug(prewarm_uvx="done")

    async def _prewarm_model(self) -> None:
        self._set_debug(prewarm_model="running")
        try:
            from huggingface_hub import snapshot_download
            from huggingface_hub.utils import disable_progress_bars
            disable_progress_bars()
            await anyio.to_thread.run_sync(snapshot_download, DEFAULT_MODEL)
        except (OSError, httpx.HTTPError) as exc:
            self._set_debug(prewarm_model=f"failed: {exc.__class__.__name__}")
            return
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
        existing = await anyio.to_thread.run_sync(self.repo.all_records)
        if not existing:
            return
        self.records = list(existing)
        _, _, total_files = await anyio.to_thread.run_sync(self.repo.stats)
        self.view.show_total_files(total_files)
        self.view.render_scores(self.records)

    def watch_stage(self, stage: Stage) -> None:
        if isinstance(stage, (Uploading, IdleEmpty, IdleCaughtUp, IdleAfterUpload)):
            self.view.hide_moments()
        if isinstance(stage, (IdleEmpty, IdleCaughtUp, IdleAfterUpload)):
            self.view.activate_cta()
        match stage:
            case Booting():
                self._update_status("[dim]Initializing...[/]")
            case Authenticating():
                self._update_status("[dim]Verifying key...[/]")
            case Discovering():
                self._update_status("[dim]Discovering transcripts...[/]")
            case Scoring():
                self._update_status(self._scoring_status_text())
            case Uploading():
                self.view.update_upload(self._upload)
                self._update_status("[dim]Scoring done. Sending the rest up to sentiments.cc...[/]")
            case IdleEmpty():
                self.view.show_stats(0, 0, 0)
                self._update_status(
                    "[green]All set. No conversations yet. Come back after using Claude Code.[/] "
                    "[dim]Press O to browse the dashboard.[/]"
                )
                self._maybe_prewarm()
            case IdleCaughtUp(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(
                    f"[green]All caught up.[/] "
                    f"{s} chat{'s' if s != 1 else ''}, "
                    f"{b} moment{'s' if b != 1 else ''} scored. "
                    f"[dim]Press R to rescan, O to open dashboard.[/]"
                )
            case IdleAfterUpload(total_buckets=b, total_sessions=s, total_files=f):
                self.view.show_stats(b, s, f)
                self._update_status(self._uploaded_status_text())
            case Error(message=m):
                self._update_status(m)
            case RescanConfirm():
                self._update_status(
                    "[yellow]Press R again within 5s to clear all state and rescan from scratch.[/]"
                )

    def _uploaded_status_text(self) -> str:
        polling = self._debug_state.card_last_status in ("polling", "idle") and self._debug_state.card_stopped is None
        suffix = "[dim]Generating your card…[/]" if polling else "[dim]Press O to open.[/]"
        return (
            "[green]Uploaded.[/] See your data at "
            f"[link='{DASHBOARD_URL}'][b]sentiments.cc[/b][/link]. "
            f"{suffix}"
        )

    def _scoring_status_text(self) -> str:
        if self.uploaded_count == 0:
            return "[dim]Scoring locally on your Mac. We'll upload each batch as it's ready.[/]"
        denom = self._upload.preseed_count + len(self.records)
        return (
            f"[dim]Scoring locally. Uploaded [b]{self.uploaded_count}[/] "
            f"of [b]{denom}[/] so far.[/]"
        )

    def watch_uploaded_count(self, uploaded_count: int) -> None:
        if isinstance(self.stage, Scoring):
            self._update_status(self._scoring_status_text())

    async def _authenticate(self) -> bool:
        while True:
            if self.state.config is None:
                ok = await self.push_screen_wait(SetupScreen(self.state))
                if not ok:
                    return False
                continue
            self.stage = Authenticating()
            self._set_boot_status("Verifying your key with the server...")
            match await Uploader().probe_credentials(self.state.config):
                case AuthOk():
                    return True
                case AuthUnauthorized():
                    self._update_status(
                        "[yellow]Server doesn't recognize this key. Let's try setup again.[/]"
                    )
                    self.state.config = None
                    await anyio.to_thread.run_sync(self.state.save)
                    continue
                case AuthUnreachable(detail=d):
                    self._debug(f"AuthUnreachable: {d}")
                    self.stage = Error(f"[red]Couldn't reach the server.[/] [dim]{d}[/]")
                    return False
                case AuthServerError(status=s):
                    self._debug(f"AuthServerError: status={s}")
                    self.stage = Error(f"[red]Server error verifying key ({s}).[/]")
                    return False

    @work()
    async def run_flow(self) -> None:
        from cc_sentiment.pipeline import Pipeline

        assert self.repo is not None

        self._set_boot_status("Choosing local engine...")
        try:
            engine = await anyio.to_thread.run_sync(EngineFactory.resolve, None)
        except RuntimeError as e:
            await self._dismiss_boot_screen()
            await self.push_screen_wait(PlatformErrorScreen(str(e)))
            self.exit()
            return
        self._set_debug(engine_name=engine)
        self._debug(f"engine={engine}")
        self._debug(f"transcript-backend: {TranscriptParser.backend_name()}")

        build_task: asyncio.Task[InferenceEngine] | None = None
        if engine == "omlx":
            build_task = asyncio.create_task(
                EngineFactory.build(
                    engine,
                    self.model_repo,
                    on_engine_log=lambda msg: self.call_from_thread(self._debug, msg),
                ),
                name="engine-build",
            )

        classifier: InferenceEngine | None = None
        try:
            self.stage = Discovering()
            self._set_boot_status("Discovering transcripts...")
            scan = await Pipeline.scan(self.repo)
            pending = await anyio.to_thread.run_sync(self.repo.pending_records)
            self._debug(f"transcripts={len(scan.transcripts)} pending={len(pending)}")

            if (scan.transcripts or pending) and not await self._authenticate():
                await self._dismiss_boot_screen()
                self.exit()
                return

            bucket_count = scan.total_new_buckets
            if scan.transcripts:
                self._set_boot_status("Sizing things up...")
                self._debug(f"bucket_count={bucket_count}")
                rate = Hardware.estimate_buckets_per_sec(engine)
                if rate and rate > 0:
                    self._update_status(
                        f"[dim]Found [b]{bucket_count:,}[/] moments. "
                        f"About {TimeFormat.format_duration(bucket_count / rate)} to score on this Mac.[/]"
                    )
                else:
                    self._update_status(f"[dim]Found [b]{bucket_count:,}[/] moments.[/]")

            if engine == "claude" and bucket_count > 0:
                ok = await self.push_screen_wait(
                    CostReviewScreen(bucket_count, self.model_repo or ClaudeCLIEngine.HAIKU_MODEL)
                )
                if not ok:
                    await self._dismiss_boot_screen()
                    self.exit()
                    return

            pre_seed = await anyio.to_thread.run_sync(self.repo.pending_records)
            has_work = (scan.transcripts and bucket_count > 0) or bool(pre_seed)
            self._upload.reset()
            self._upload.preseed_count = len(pre_seed)

            needs_classifier = bool(scan.transcripts) and bucket_count > 0
            if needs_classifier:
                if build_task is not None:
                    if not build_task.done():
                        self._set_boot_status("Almost ready — warming up the local model...")
                    try:
                        classifier = await build_task
                    except (TimeoutError, OSError, RuntimeError) as exc:
                        await self._dismiss_boot_screen()
                        self.stage = Error(
                            f"[red]Couldn't start the local model.[/] [dim]{exc}[/]"
                        )
                        return
                    build_task = None
                else:
                    classifier = await EngineFactory.build(engine, self.model_repo)

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
                            app=self,
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
                        "Run [b]cc-sentiment setup[/] again, or upload your key to GitHub/keys.openpgp.org."
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

            uploaded = self.uploaded_count > 0
            await self._enter_idle(uploaded=uploaded)

            if uploaded:
                assert self.state.config is not None
                self.run_worker(self._poll_card(self.state.config), name="card-poll", exclusive=True, exit_on_error=False)
        finally:
            if build_task is not None:
                if not build_task.done():
                    build_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await build_task
            if classifier is not None:
                await classifier.close()

    def _on_upload_progress_change(self, progress: UploadProgress) -> None:
        self.uploaded_count = progress.uploaded_records
        self.view.update_upload(progress)

    async def _poll_card(self, config: SSHConfig | GPGConfig | GistConfig) -> None:
        poller = CardPoller(
            config=config,
            on_ready=self._on_card_ready,
            on_state=self._on_card_state,
        )
        await poller.run()
        if isinstance(self.stage, IdleAfterUpload) and self._debug_state.card_stopped != "ready":
            self._update_status(self._uploaded_status_text())

    def _on_card_ready(self, stat: MyStat) -> None:
        if self.state.config is None:
            return
        self.view.set_tweet(self.state.config, stat)
        self.push_screen(StatShareScreen(self.state.config, stat))
        if isinstance(self.stage, IdleAfterUpload):
            self._update_status(self._uploaded_status_text())

    @on(Button.Pressed, "#cta-action")
    async def on_cta_action(self) -> None:
        cta = self.view.cta
        match cta.showing:
            case "tweet":
                assert cta.tweet_config is not None and cta.tweet_stat is not None
                self.push_screen(StatShareScreen(cta.tweet_config, cta.tweet_stat))
            case "schedule":
                await self._install_daemon()

    def _on_card_state(self, attempts: int, status: str, elapsed: float, stopped: str | None) -> None:
        self._set_debug(
            card_attempts=attempts,
            card_last_status=status,
            card_elapsed=elapsed,
            card_stopped=stopped,
        )

    async def _install_daemon(self) -> None:
        try:
            await anyio.to_thread.run_sync(LaunchAgent.install)
        except subprocess.CalledProcessError as e:
            self._update_status(
                f"[yellow]Couldn't schedule the background job ({e.returncode}).[/] "
                "[dim]Try `cc-sentiment install` manually.[/]"
            )
            return
        self.view.set_schedule_available(False)
        self._update_status(
            "[green]Scheduled.[/] It'll refresh your numbers daily in the background. "
            "[dim]Undo with `cc-sentiment uninstall`.[/]"
        )

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

    async def action_open_dashboard(self) -> None:
        await anyio.to_thread.run_sync(webbrowser.open, DASHBOARD_URL)
        self._update_status(f"[dim]Opened {DASHBOARD_URL}.[/]")
        self.set_timer(3.0, lambda: self.watch_stage(self.stage))

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
        await anyio.to_thread.run_sync(self.repo.clear_all)
        self.records = []
        self.scored = 0
        self.total = 0
        self.uploaded_count = 0
        self._scoring.reset()
        self._upload.reset()
        self._debug_state.reset()
        self.view.reset()

    def _begin_scoring(self, total: int, engine: str, total_files: int) -> None:
        self.total = total
        self.scored = 0
        self._scoring.begin(Hardware.estimate_buckets_per_sec(engine), total)
        self.view.begin_scoring(total, total_files)
        self.view.update_progress_label(self._scoring, self.scored, self.total)
        self.stage = Scoring(total=total, engine=engine)

    def _add_buckets(self, n: int) -> None:
        asyncio.get_running_loop()
        self.scored += n
        self.view.bump_scored(self.scored, self._scoring, self.total)

    def _add_records(self, new_records: list[SentimentRecord]) -> None:
        asyncio.get_running_loop()
        self.records.extend(new_records)
        self.view.render_scores(self.records)

    def _track_frustration(self, words: list[str]) -> None:
        asyncio.get_running_loop()
        self.view.track_frustration(words)

    def _update_status(self, text: str) -> None:
        self.status_text = text
        self.view.update_status(text)

    def _append_status(self, addition: str) -> None:
        self._update_status(f"{self.status_text}\n{addition}".strip())
