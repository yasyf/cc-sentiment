from __future__ import annotations

import subprocess
import sys
import traceback
from dataclasses import dataclass

import anyio.to_thread
import httpx

from cc_sentiment.engines import resolve_engine
from cc_sentiment.models import AppState
from cc_sentiment.pipeline import Pipeline
from cc_sentiment.repo import Repository
from cc_sentiment.transcripts import TranscriptParser
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


UploadError = (
    httpx.HTTPStatusError | httpx.ConnectError | httpx.TimeoutException
    | httpx.NetworkError | subprocess.CalledProcessError
)


class HeadlessRunner:
    @staticmethod
    def trace(debug: bool, msg: str) -> None:
        if debug:
            print(f"debug: {msg}", file=sys.stderr)

    @staticmethod
    def upload_error_detail(error: UploadError) -> str:
        match error:
            case httpx.HTTPStatusError():
                return f"server rejected upload ({error.response.status_code})"
            case subprocess.CalledProcessError():
                return f"signing failed ({error.returncode})"
            case _:
                return f"couldn't reach server: {error}"

    @classmethod
    async def run(cls, state: AppState, repo: Repository, debug: bool = False) -> HeadlessOutcome:
        if state.config is None:
            return HeadlessNotConfigured()

        engine = await anyio.to_thread.run_sync(resolve_engine, None)
        cls.trace(debug, f"engine={engine}")
        cls.trace(debug, f"transcript-backend: {TranscriptParser.backend_name()}")
        if engine == "claude":
            return HeadlessClaudeEngineBlocked()

        scan = await Pipeline.scan(repo)
        pending = await anyio.to_thread.run_sync(repo.pending_records)
        cls.trace(debug, f"transcripts={len(scan.transcripts)} pending={len(pending)}")
        if not scan.transcripts and not pending:
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
        if scan.transcripts:
            records = await Pipeline.run(repo, scan, engine=engine)
            scored = len(records)

        pending = await anyio.to_thread.run_sync(repo.pending_records)
        if not pending:
            return HeadlessOk(scored=scored, uploaded=0)

        try:
            await uploader.upload(pending, state, repo)
        except (
            httpx.HTTPStatusError, httpx.ConnectError, httpx.TimeoutException,
            httpx.NetworkError, subprocess.CalledProcessError,
        ) as e:
            if debug:
                traceback.print_exc(file=sys.stderr)
            return HeadlessUploadError(detail=cls.upload_error_detail(e))

        return HeadlessOk(scored=scored, uploaded=len(pending))
