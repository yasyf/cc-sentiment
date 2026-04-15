from __future__ import annotations

from dataclasses import dataclass

import anyio.to_thread
import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cc_sentiment.models import (
    AppState,
    GPGConfig,
    SentimentRecord,
    SSHConfig,
    UploadPayload,
)
from cc_sentiment.repo import Repository
from cc_sentiment.signing import GPGBackend, PayloadSigner, SigningBackend, SSHBackend

DEFAULT_SERVER_URL = "https://anetaco--cc-sentiment-api-serve.modal.run"

TEST_PAYLOAD = "cc-sentiment-verify"

RETRYABLE_STATUS_CODES = {429, 502, 503, 504}

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
    def backend_from_config(config: SSHConfig | GPGConfig) -> SigningBackend:
        match config:
            case SSHConfig(key_path=p):
                return SSHBackend(private_key_path=p)
            case GPGConfig(fpr=f):
                return GPGBackend(fpr=f)

    @_retry
    async def _verify_credentials(self, config: SSHConfig | GPGConfig) -> None:
        backend = self.backend_from_config(config)
        signature = await anyio.to_thread.run_sync(PayloadSigner.sign, TEST_PAYLOAD, backend)
        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/verify",
                json={
                    "contributor_type": config.contributor_type,
                    "contributor_id": config.contributor_id,
                    "signature": signature,
                    "test_payload": TEST_PAYLOAD,
                },
                timeout=15.0,
            )).raise_for_status()

    async def probe_credentials(self, config: SSHConfig | GPGConfig) -> AuthResult:
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
        self, records: list[SentimentRecord], state: AppState, repo: Repository
    ) -> None:
        assert state.config is not None, "Client not configured. Run 'cc-sentiment setup' first."

        backend = self.backend_from_config(state.config)
        signature = await anyio.to_thread.run_sync(PayloadSigner.sign_records, records, backend)
        payload = UploadPayload(
            contributor_type=state.config.contributor_type,
            contributor_id=state.config.contributor_id,
            signature=signature,
            records=tuple(records),
        )

        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/upload",
                json=payload.model_dump(mode="json", by_alias=True),
                timeout=30.0,
            )).raise_for_status()

        await anyio.to_thread.run_sync(
            repo.mark_sessions_uploaded, {r.conversation_id for r in records}
        )
