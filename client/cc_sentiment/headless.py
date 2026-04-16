from __future__ import annotations

import subprocess
from dataclasses import dataclass

import anyio.to_thread
import httpx

from cc_sentiment.engines import resolve_engine
from cc_sentiment.models import AppState
from cc_sentiment.pipeline import Pipeline
from cc_sentiment.repo import Repository
from cc_sentiment.upload import (
    AuthOk,
    AuthServerError,
    AuthUnauthorized,
    AuthUnreachable,
    Uploader,
)


@dataclass(frozen=True)
class HeadlessOk:
    scored: int
    uploaded: int


@dataclass(frozen=True)
class HeadlessNothingToDo:
    pass


@dataclass(frozen=True)
class HeadlessNotConfigured:
    pass


@dataclass(frozen=True)
class HeadlessClaudeEngineBlocked:
    pass


@dataclass(frozen=True)
class HeadlessAuthError:
    detail: str


@dataclass(frozen=True)
class HeadlessUploadError:
    detail: str


HeadlessOutcome = (
    HeadlessOk
    | HeadlessNothingToDo
    | HeadlessNotConfigured
    | HeadlessClaudeEngineBlocked
    | HeadlessAuthError
    | HeadlessUploadError
)


class HeadlessRunner:
    @classmethod
    async def run(cls, state: AppState, repo: Repository) -> HeadlessOutcome:
        if state.config is None:
            return HeadlessNotConfigured()

        engine = await anyio.to_thread.run_sync(resolve_engine, None)
        if engine == "claude":
            return HeadlessClaudeEngineBlocked()

        transcripts = await anyio.to_thread.run_sync(
            Pipeline.discover_new_transcripts, repo
        )
        pending = await anyio.to_thread.run_sync(repo.pending_records)
        if not transcripts and not pending:
            return HeadlessNothingToDo()

        uploader = Uploader()
        match await uploader.probe_credentials(state.config):
            case AuthOk():
                pass
            case AuthUnauthorized(status=s):
                return HeadlessAuthError(
                    detail=f"server rejected the signing key ({s}); re-run `cc-sentiment setup`"
                )
            case AuthUnreachable(detail=d):
                return HeadlessAuthError(detail=f"couldn't reach the server: {d}")
            case AuthServerError(status=s):
                return HeadlessAuthError(detail=f"server error verifying key ({s})")

        scored = 0
        if transcripts:
            records = await Pipeline.run(repo, engine, None, transcripts)
            scored = len(records)

        pending = await anyio.to_thread.run_sync(repo.pending_records)
        uploaded = 0
        if pending:
            try:
                await uploader.upload(pending, state, repo)
            except httpx.HTTPStatusError as e:
                return HeadlessUploadError(
                    detail=f"server rejected upload ({e.response.status_code})"
                )
            except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError) as e:
                return HeadlessUploadError(detail=f"couldn't reach server: {e}")
            except subprocess.CalledProcessError as e:
                return HeadlessUploadError(detail=f"signing failed ({e.returncode})")
            uploaded = len(pending)

        return HeadlessOk(scored=scored, uploaded=uploaded)
