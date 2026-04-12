from __future__ import annotations

import httpx

from client.models import (
    AppState,
    SentimentRecord,
    SessionId,
    UploadPayload,
)
from client.signing import PayloadSigner

DEFAULT_SERVER_URL = "https://cc-sentiment.modal.run"


class Uploader:
    def __init__(self, server_url: str = DEFAULT_SERVER_URL) -> None:
        self.server_url = server_url

    async def upload(
        self,
        records: list[SentimentRecord],
        state: AppState,
    ) -> None:
        assert state.config is not None
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
                json=payload.model_dump(mode="json"),
                timeout=30.0,
            )
            response.raise_for_status()

        uploaded_sessions: set[SessionId] = {r.conversation_id for r in records}
        for session_id in uploaded_sessions:
            if session_id in state.processed:
                prev = state.processed[session_id]
                state.processed[session_id] = prev.model_copy(
                    update={"uploaded": True}
                )
        state.save()

    def pending_records(self, state: AppState) -> list[SessionId]:
        return [
            sid
            for sid, info in state.processed.items()
            if not info.uploaded
        ]
