from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import anyio.to_thread
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cc_sentiment.models import (
    AppState,
    GistConfig,
    GPGConfig,
    MyStat,
    SentimentRecord,
    SSHConfig,
    UploadPayload,
)
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGBackend, PayloadSigner, SigningBackend, SSHBackend

DEFAULT_SERVER_URL = "https://anetaco--cc-sentiment-api-serve.modal.run"

TEST_PAYLOAD = "cc-sentiment-verify"

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

UPLOAD_CHUNK_BYTES = 16 * 1024

NOOP_UPLOAD_PROGRESS: Callable[[float], None] = lambda _: None

_retry = retry(
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

    @_retry
    async def _verify_credentials(self, config: SSHConfig | GPGConfig | GistConfig) -> None:
        backend = self.backend_from_config(config)
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
        return AuthOk()

    @_retry
    async def upload(
        self,
        records: list[SentimentRecord],
        state: AppState,
        repo: Repository,
        on_progress: Callable[[float], None] = NOOP_UPLOAD_PROGRESS,
    ) -> None:
        assert state.config is not None, "Client not configured. Run 'cc-sentiment setup' first."

        backend = self.backend_from_config(state.config)
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

    async def fetch_my_stat(self, config: SSHConfig | GPGConfig | GistConfig) -> MyStat | None:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{self.server_url}/my-stats",
                params={"contributor_id": config.contributor_id},
                timeout=15.0,
            )
        match response.status_code:
            case 200:
                return MyStat.model_validate_json(response.text)
            case 404:
                return None
            case _:
                response.raise_for_status()
                return None
