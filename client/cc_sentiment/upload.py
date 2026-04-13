from __future__ import annotations

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from cc_sentiment.models import (
    AppState,
    ClientConfig,
    SentimentRecord,
    SessionId,
    UploadPayload,
)
from cc_sentiment.signing import PayloadSigner

DEFAULT_SERVER_URL = "https://cc-sentiment.modal.run"

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


class Uploader:
    def __init__(self, server_url: str = DEFAULT_SERVER_URL) -> None:
        self.server_url = server_url

    @staticmethod
    def records_from_state(state: AppState) -> list[SentimentRecord]:
        return [
            record
            for session in state.sessions.values()
            if not session.uploaded
            for record in session.records
        ]

    @_retry
    async def verify_credentials(self, config: ClientConfig) -> None:
        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/verify",
                json={
                    "github_username": config.github_username,
                    "signature": PayloadSigner.sign(TEST_PAYLOAD, config.key_path),
                    "test_payload": TEST_PAYLOAD,
                },
                timeout=15.0,
            )).raise_for_status()

    @_retry
    async def upload(self, records: list[SentimentRecord], state: AppState) -> None:
        assert state.config is not None, "Client not configured. Run 'cc-sentiment setup' first."

        payload = UploadPayload(
            github_username=state.config.github_username,
            signature=PayloadSigner.sign_records(records, state.config.key_path),
            records=tuple(records),
        )

        async with httpx.AsyncClient() as client:
            (await client.post(
                f"{self.server_url}/upload",
                json=payload.model_dump(mode="json", by_alias=True),
                timeout=30.0,
            )).raise_for_status()

        for session_id in {r.conversation_id for r in records}:
            if session_id in state.sessions:
                state.sessions[session_id] = state.sessions[session_id].model_copy(update={"uploaded": True})
        state.save()
