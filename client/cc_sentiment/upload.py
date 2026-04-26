from __future__ import annotations

import subprocess
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import anyio
import anyio.streams.memory
import anyio.to_thread
import httpx
import orjson
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cc_sentiment.models import (
    CLIENT_VERSION,
    AppState,
    DaemonEvent,
    DaemonEventPayload,
    DaemonEventType,
    GistConfig,
    GPGConfig,
    MyStat,
    SentimentRecord,
    ShareMintPayload,
    ShareMintRequest,
    ShareMintResponse,
    SSHConfig,
    UploadPayload,
)
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGBackend, PayloadSigner, SigningBackend, SSHBackend

DEFAULT_SERVER_URL = "https://anetaco--cc-sentiment-api-serve.modal.run"

DASHBOARD_URL = "https://sentiments.cc"

TWEET_INTENT_URL = "https://twitter.com/intent/tweet"

TEST_PAYLOAD = "cc-sentiment-verify"

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

UPLOAD_CHUNK_BYTES = 16 * 1024

WORKER_BATCH_RETRIES = 3

WORKER_BACKOFF_BASE_SECONDS = 5

UPLOAD_WORKER_COUNT = 8

UPLOAD_POOL_TIMEOUT_SECONDS = 600

UPLOAD_BATCH_TARGET_RECORDS = 500

UPLOAD_BATCH_MAX_AGE_SECONDS = 10.0

UPLOAD_BATCH_TIMER_TICK_SECONDS = 1.0

NOOP_UPLOAD_PROGRESS: Callable[[float], None] = lambda _: None


@dataclass
class UploadProgress:
    preseed_count: int = 0
    queued_batches: int = 0
    in_flight_batches: int = 0
    done_batches: int = 0
    failed_batches: int = 0
    queued_records: int = 0
    uploaded_records: int = 0
    fatal: BaseException | None = None
    started_at: float = 0.0

    @property
    def waiting_batches(self) -> int:
        return max(0, self.queued_batches - self.done_batches - self.in_flight_batches)

    @property
    def all_settled(self) -> bool:
        return self.queued_batches > 0 and (self.done_batches + self.failed_batches) == self.queued_batches

    def records_queued(self, count: int) -> None:
        self.queued_records += count

    def batch_queued(self) -> None:
        self.queued_batches += 1

    def batch_started(self) -> None:
        self.in_flight_batches += 1

    def batch_finished(self) -> None:
        self.in_flight_batches -= 1

    def batch_done(self, batch_size: int) -> None:
        self.done_batches += 1
        self.uploaded_records += batch_size

    def batch_failed(self) -> None:
        self.failed_batches += 1

    def reset(self) -> None:
        self.preseed_count = 0
        self.queued_batches = 0
        self.in_flight_batches = 0
        self.done_batches = 0
        self.failed_batches = 0
        self.queued_records = 0
        self.uploaded_records = 0
        self.fatal = None
        self.started_at = 0.0

RETRY_POLICY = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    retry=retry_if_exception(lambda exc: (
        isinstance(exc, (httpx.ConnectError, httpx.TimeoutException))
        or (isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in RETRYABLE_STATUS_CODES)
    )),
    reraise=True,
)


@dataclass(frozen=True)
class AuthOk:
    pass


@dataclass(frozen=True)
class AuthUnauthorized:
    status: int
    detail: str = ""


@dataclass(frozen=True)
class AuthUnreachable:
    detail: str


@dataclass(frozen=True)
class AuthServerError:
    status: int


AuthResult = AuthOk | AuthUnauthorized | AuthUnreachable | AuthServerError


class Uploader:
    def __init__(self, server_url: str = DEFAULT_SERVER_URL) -> None:
        self.server_url = server_url

    @staticmethod
    def backend_from_config(config: SSHConfig | GPGConfig | GistConfig) -> SigningBackend:
        match config:
            case SSHConfig(key_path=p):
                return SSHBackend(private_key_path=p)
            case GPGConfig(fpr=f):
                return GPGBackend(fpr=f)
            case GistConfig(key_path=p):
                return SSHBackend(private_key_path=p)

    @staticmethod
    def wire_contributor_id(config: SSHConfig | GPGConfig | GistConfig) -> str:
        match config:
            case GistConfig(contributor_id=u, gist_id=g):
                return f"{u}/{g}"
            case _:
                return config.contributor_id

    @RETRY_POLICY
    async def _verify_credentials(self, config: SSHConfig | GPGConfig | GistConfig) -> None:
        backend = self.backend_from_config(config)
        with anyio.fail_after(10):
            signature = await anyio.to_thread.run_sync(PayloadSigner.sign, TEST_PAYLOAD, backend)
        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/verify",
                json={
                    "contributor_type": config.contributor_type,
                    "contributor_id": self.wire_contributor_id(config),
                    "signature": signature,
                    "test_payload": TEST_PAYLOAD,
                },
                timeout=15.0,
            )).raise_for_status()

    async def probe_credentials(self, config: SSHConfig | GPGConfig | GistConfig) -> AuthResult:
        try:
            await self._verify_credentials(config)
        except httpx.HTTPStatusError as e:
            if e.response.status_code in (401, 403):
                return AuthUnauthorized(status=e.response.status_code)
            return AuthServerError(status=e.response.status_code)
        except (httpx.ConnectError, httpx.TimeoutException) as e:
            return AuthUnreachable(detail=str(e))
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError, AssertionError) as e:
            return AuthUnauthorized(status=0, detail=f"local signing failed: {e.__class__.__name__}: {e}")
        return AuthOk()

    async def upload(
        self,
        records: list[SentimentRecord],
        state: AppState,
        repo: Repository,
        on_progress: Callable[[float], None] = NOOP_UPLOAD_PROGRESS,
    ) -> None:
        assert state.config is not None, "Client not configured. Run 'cc-sentiment setup' first."

        backend = self.backend_from_config(state.config)
        with anyio.fail_after(10):
            signature = await anyio.to_thread.run_sync(PayloadSigner.sign_records, records, backend)
        payload = UploadPayload(
            contributor_type=state.config.contributor_type,
            contributor_id=self.wire_contributor_id(state.config),
            signature=signature,
            records=tuple(records),
        )
        body = payload.model_dump_json(by_alias=True).encode()
        total = len(body)

        async def stream() -> AsyncIterator[bytes]:
            for i in range(0, total, UPLOAD_CHUNK_BYTES):
                chunk = body[i:i + UPLOAD_CHUNK_BYTES]
                yield chunk
                on_progress((i + len(chunk)) / total)

        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/upload",
                content=stream(),
                headers={"Content-Type": "application/json", "Content-Length": str(total)},
                timeout=30.0,
            )).raise_for_status()

        await anyio.to_thread.run_sync(
            repo.mark_sessions_uploaded, {r.conversation_id for r in records}
        )

    async def record_daemon_event(
        self,
        config: SSHConfig | GPGConfig | GistConfig,
        event_type: DaemonEventType,
    ) -> None:
        event = DaemonEvent(
            event_type=event_type,
            client_version=CLIENT_VERSION,
            time=datetime.now(timezone.utc),
        )
        canonical = orjson.dumps(
            event.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS
        ).decode()
        backend = self.backend_from_config(config)
        with anyio.fail_after(10):
            signature = await anyio.to_thread.run_sync(PayloadSigner.sign, canonical, backend)
        payload = DaemonEventPayload(
            contributor_type=config.contributor_type,
            contributor_id=self.wire_contributor_id(config),
            signature=signature,
            event=event,
        )
        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/daemon-event",
                content=payload.model_dump_json().encode(),
                headers={"Content-Type": "application/json"},
                timeout=3.0,
            )).raise_for_status()

    @classmethod
    async def ping_daemon_event(cls, event_type: DaemonEventType) -> None:
        if (config := AppState.load().config) is None:
            return
        with anyio.fail_after(3.0):
            await cls().record_daemon_event(config, event_type)

    async def fetch_my_stat(self, config: SSHConfig | GPGConfig | GistConfig) -> MyStat | None:
        return await anyio.to_thread.run_sync(self.fetch_my_stat_sync, config)

    def fetch_my_stat_sync(self, config: SSHConfig | GPGConfig | GistConfig) -> MyStat | None:
        with httpx.Client(timeout=15.0) as client:
            response = client.get(
                f"{self.server_url}/my-stats",
                params={"contributor_id": config.contributor_id},
            )
        match response.status_code:
            case 200:
                return MyStat.model_validate_json(response.text)
            case 404:
                return None
            case _:
                response.raise_for_status()
                return None

    async def mint_share(
        self, config: SSHConfig | GPGConfig | GistConfig
    ) -> ShareMintResponse:
        mint_payload = ShareMintPayload(issued_at=datetime.now(timezone.utc))
        canonical = orjson.dumps(
            mint_payload.model_dump(mode="json"), option=orjson.OPT_SORT_KEYS
        ).decode()
        backend = self.backend_from_config(config)
        with anyio.fail_after(10):
            signature = await anyio.to_thread.run_sync(PayloadSigner.sign, canonical, backend)
        request = ShareMintRequest(
            contributor_type=config.contributor_type,
            contributor_id=self.wire_contributor_id(config),
            signature=signature,
            payload=mint_payload,
        )
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/share",
                content=request.model_dump_json().encode(),
                headers={"Content-Type": "application/json"},
                timeout=15.0,
            )
        response.raise_for_status()
        return ShareMintResponse.model_validate_json(response.text)

    @staticmethod
    def share_url(share_id: str) -> str:
        return f"{DASHBOARD_URL}/share/{share_id}"

    @classmethod
    def tweet_url(cls, share_id: str, tweet_text: str) -> str:
        return f"{TWEET_INTENT_URL}?{urlencode({'text': tweet_text, 'url': cls.share_url(share_id)})}"


class UploadPool:
    def __init__(
        self,
        uploader: Uploader,
        state: AppState,
        repo: Repository,
        progress: UploadProgress,
        on_progress_change: Callable[[UploadProgress], None],
        debug: Callable[[str], None] = lambda _: None,
    ) -> None:
        self.uploader = uploader
        self.state = state
        self.repo = repo
        self.progress = progress
        self.on_progress_change = on_progress_change
        self.debug = debug
        self._send_stream, self._recv_stream = anyio.create_memory_object_stream[
            list[SentimentRecord]
        ](float("inf"))
        self._buffer: list[SentimentRecord] = []
        self._buffer_started_at: float = 0.0

    def queue_records(self, records: list[SentimentRecord]) -> None:
        if not self._buffer:
            self._buffer_started_at = time.monotonic()
        self._buffer.extend(records)
        self.progress.records_queued(len(records))
        if len(self._buffer) >= UPLOAD_BATCH_TARGET_RECORDS:
            self.flush()
        else:
            self.on_progress_change(self.progress)

    def flush(self) -> None:
        if not self._buffer:
            return
        batch, self._buffer = self._buffer, []
        self._buffer_started_at = 0.0
        self.progress.batch_queued()
        self._send_stream.send_nowait(batch)
        self.debug(f"upload: flushed batch of {len(batch)} records")
        self.on_progress_change(self.progress)

    async def run(self, producer: Callable[[], Awaitable[None]]) -> None:
        self.progress.started_at = time.monotonic()
        with anyio.fail_after(UPLOAD_POOL_TIMEOUT_SECONDS):
            async with anyio.create_task_group() as tg:
                for worker_id in range(UPLOAD_WORKER_COUNT):
                    tg.start_soon(self._worker_loop, self._recv_stream.clone(), worker_id)
                self._recv_stream.close()
                try:
                    async with anyio.create_task_group() as timer_tg:
                        timer_tg.start_soon(self._flush_timer)
                        try:
                            await producer()
                        finally:
                            self.flush()
                            timer_tg.cancel_scope.cancel()
                finally:
                    self._send_stream.close()
        self.debug(
            f"upload: done — {self.progress.done_batches} ok, "
            f"{self.progress.failed_batches} failed, "
            f"elapsed {time.monotonic() - self.progress.started_at:.1f}s"
        )

    async def _flush_timer(self) -> None:
        while True:
            await anyio.sleep(UPLOAD_BATCH_TIMER_TICK_SECONDS)
            if self._buffer and (time.monotonic() - self._buffer_started_at) >= UPLOAD_BATCH_MAX_AGE_SECONDS:
                self.flush()

    async def _worker_loop(
        self,
        recv_stream: anyio.streams.memory.MemoryObjectReceiveStream[list[SentimentRecord]],
        worker_id: int,
    ) -> None:
        started_at = time.monotonic()
        done_here = 0
        self.debug(f"upload: worker {worker_id} started")
        async with recv_stream:
            async for batch in recv_stream:
                if self.progress.fatal is not None:
                    continue
                await self._upload_one_batch(batch, worker_id)
                done_here += 1
        self.debug(
            f"upload: worker {worker_id} exiting (stream closed); "
            f"uploaded {done_here} batches in {time.monotonic() - started_at:.1f}s"
        )

    async def _upload_one_batch(self, batch: list[SentimentRecord], worker_id: int) -> None:
        self.progress.batch_started()
        self.on_progress_change(self.progress)
        self.debug(
            f"upload: worker {worker_id} → batch of {len(batch)} records; "
            f"queue={self.progress.in_flight_batches} in flight, "
            f"{self.progress.waiting_batches} waiting"
        )
        last_exc: BaseException | None = None
        try:
            for attempt in range(WORKER_BATCH_RETRIES):
                post_start = time.monotonic()
                try:
                    await self.uploader.upload(batch, self.state, self.repo)
                except httpx.HTTPStatusError as e:
                    if e.response.status_code in (401, 403):
                        self.debug(
                            f"upload: worker {worker_id} fatal: "
                            f"HTTPStatusError {e.response.status_code}"
                        )
                        self.progress.fatal = e
                        return
                    last_exc = e
                except subprocess.CalledProcessError as e:
                    self.debug(f"upload: worker {worker_id} fatal: {type(e).__name__}: {e}")
                    self.progress.fatal = e
                    return
                except (httpx.NetworkError, httpx.TimeoutException) as e:
                    last_exc = e
                else:
                    post_ms = (time.monotonic() - post_start) * 1000
                    self.debug(
                        f"upload: worker {worker_id} posted in {post_ms:.0f}ms (HTTP 200)"
                    )
                    self.progress.batch_done(len(batch))
                    return
                if attempt == WORKER_BATCH_RETRIES - 1:
                    self.debug(
                        f"upload: worker {worker_id} abandoned batch after "
                        f"{WORKER_BATCH_RETRIES} attempts "
                        f"({type(last_exc).__name__}: {last_exc})"
                    )
                    self.progress.batch_failed()
                    return
                delay = WORKER_BACKOFF_BASE_SECONDS * 3 ** attempt
                self.debug(
                    f"upload: worker {worker_id} retry {attempt + 1}/{WORKER_BATCH_RETRIES} "
                    f"({type(last_exc).__name__}: {last_exc}); sleeping {delay:.1f}s"
                )
                await anyio.sleep(delay)
        finally:
            self.progress.batch_finished()
            self.on_progress_change(self.progress)
