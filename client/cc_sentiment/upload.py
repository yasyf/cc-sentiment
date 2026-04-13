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


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, (httpx.ConnectError, httpx.TimeoutException)):
        return True
    if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code in (429, 502, 503, 504):
        return True
    return False


_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, max=8),
    retry=retry_if_exception(is_retryable),
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
        signature = PayloadSigner.sign(TEST_PAYLOAD, config.key_path)
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/verify",
                json={
                    "github_username": config.github_username,
                    "signature": signature,
                    "test_payload": TEST_PAYLOAD,
                },
                timeout=15.0,
            )
            response.raise_for_status()

    @_retry
    async def upload(
        self,
        records: list[SentimentRecord],
        state: AppState,
    ) -> None:
        if state.config is None:
            raise ValueError("Client not configured. Run 'cc-sentiment setup' first.")
        config = state.config

        signature = PayloadSigner.sign_records(records, config.key_path)
        payload = UploadPayload(
            github_username=config.github_username,
            signature=signature,
            records=tuple(records),
        )

        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.server_url}/upload",
                json=payload.model_dump(mode="json", by_alias=True),
                timeout=30.0,
            )
            response.raise_for_status()

        uploaded_sessions: set[SessionId] = {r.conversation_id for r in records}
        for session_id in uploaded_sessions:
            if session_id in state.sessions:
                prev = state.sessions[session_id]
                state.sessions[session_id] = prev.model_copy(
                    update={"uploaded": True}
                )
        state.save()
