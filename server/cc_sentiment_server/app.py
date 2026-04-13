from __future__ import annotations

import json
import os
import time
from typing import Callable, Protocol

import modal
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from cc_sentiment_server.db import Database
from cc_sentiment_server.models import (
    DataResponse,
    StatusResponse,
    UploadPayload,
    UploadResponse,
    VerifyRequest,
)
from cc_sentiment_server.verify import Verifier

__all__ = ["app", "create_app"]

DATA_CACHE_TTL_SECONDS = 60


class Cache(Protocol):
    async def get(self, key: str) -> object: ...
    async def put(self, key: str, value: object) -> None: ...


class DictCache:
    def __init__(self) -> None:
        self.d: dict = {}

    async def get(self, key: str) -> object:
        return self.d[key]

    async def put(self, key: str, value: object) -> None:
        self.d[key] = value


class ModalDictCache:
    def __init__(self, modal_dict: modal.Dict) -> None:
        self.modal_dict = modal_dict

    async def get(self, key: str) -> object:
        if (result := await self.modal_dict.get.aio(key)) is None:
            raise KeyError(key)
        return result

    async def put(self, key: str, value: object) -> None:
        await self.modal_dict.put.aio(key, value)


def create_app(
    db: Database,
    verifier: Verifier,
    data_cache: Cache,
    allowed_origins: list[str],
) -> FastAPI:
    limiter = Limiter(key_func=get_remote_address)

    web_app = FastAPI(title="cc-sentiment", docs_url=None, redoc_url=None)
    web_app.state.limiter = limiter
    web_app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

    @web_app.exception_handler(ValueError)
    async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=400)

    web_app.add_middleware(SlowAPIMiddleware)
    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )

    @web_app.post("/verify")
    @limiter.limit("10/minute")
    async def verify(request: Request, body: VerifyRequest) -> StatusResponse:
        if not await verifier.verify_signature(body.github_username, body.test_payload, body.signature):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)
        return StatusResponse()

    @web_app.post("/upload")
    @limiter.limit("100/minute")
    async def upload(request: Request, payload: UploadPayload) -> UploadResponse:
        if not await verifier.verify_signature(
            payload.github_username,
            json.dumps(
                [r.model_dump(mode="json") for r in payload.records],
                sort_keys=True,
                separators=(",", ":"),
            ),
            payload.signature,
        ):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)

        await db.ingest(payload.records, payload.github_username)
        return UploadResponse(ingested=len(payload.records))

    @web_app.get("/data")
    @limiter.limit("120/minute")
    async def data(
        request: Request,
        days: int = Query(default=7, ge=1, le=365),
    ) -> DataResponse:
        try:
            cached_at, cached_response = await data_cache.get(f"data:{days}")
            if time.time() - cached_at < DATA_CACHE_TTL_SECONDS:
                return JSONResponse(cached_response, headers={"Cache-Control": "public, max-age=3600"})
        except KeyError:
            pass

        result = await db.query_all(days)
        await data_cache.put(f"data:{days}", (time.time(), result.model_dump(mode="json")))
        return JSONResponse(result.model_dump(mode="json"), headers={"Cache-Control": "public, max-age=3600"})

    return web_app


app = modal.App("cc-sentiment")

image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install("psycopg[binary]", "psycopg_pool", "pydantic", "httpx", "fastapi[standard]", "slowapi")
    .apt_install("openssh-client")
    .add_local_python_source("cc_sentiment_server")
)


@app.cls(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")], scaledown_window=120)
@modal.concurrent(max_inputs=100)
class API:
    db: Database
    verifier: Verifier
    data_cache: ModalDictCache

    @modal.enter()
    async def startup(self) -> None:
        self.db = Database(os.environ["TIMESCALE_DSN"])
        await self.db.open()
        self.verifier = Verifier(key_cache=modal.Dict.from_name("github-keys", create_if_missing=True))
        self.data_cache = ModalDictCache(modal.Dict.from_name("data-cache", create_if_missing=True))

    @modal.exit()
    async def shutdown(self) -> None:
        await self.db.close()

    @modal.asgi_app()
    def serve(self) -> Callable:
        return create_app(self.db, self.verifier, self.data_cache, os.environ["ALLOWED_ORIGINS"].split(","))


@app.function(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")])
async def seed() -> None:
    db = Database(os.environ["TIMESCALE_DSN"])
    await db.open()
    try:
        await db.seed()
        print("Seed complete: table, hypertable, indexes, continuous aggregate, compression policy.")
    finally:
        await db.close()


@app.local_entrypoint()
async def run_seed() -> None:
    await seed.remote.aio()
