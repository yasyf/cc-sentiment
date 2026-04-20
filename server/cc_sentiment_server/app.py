from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, ClassVar, Protocol

import httpx
import modal
from fastapi import FastAPI, Header, Path, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from cc_sentiment_server.db import Database
from cc_sentiment_server.models import (
    DaemonEventPayload,
    DataResponse,
    MyStatResponse,
    ShareMintRequest,
    ShareMintResponse,
    ShareRecord,
    StatusResponse,
    UploadPayload,
    UploadResponse,
    VerifyRequest,
)
from cc_sentiment_server.utils import noop
from cc_sentiment_server.verify import ModalKeyCache, Verifier

__all__ = ["app", "create_app"]


class Cache(Protocol):
    async def get(self, key: str) -> object: ...
    async def put(self, key: str, value: object) -> None: ...


class RefreshSpawner(Protocol):
    async def __call__(self, days: int) -> None: ...


class MyStatSpawner(Protocol):
    async def __call__(self, contributor_id: str) -> None: ...


class RevalidateSpawner(Protocol):
    async def __call__(self, tag: str) -> None: ...


class ShareWarmSpawner(Protocol):
    async def __call__(self, share_id: str) -> None: ...


@dataclass
class DictCache:
    data: dict[str, object] = field(default_factory=dict)

    async def get(self, key: str) -> object:
        return self.data[key]

    async def put(self, key: str, value: object) -> None:
        self.data[key] = value


@dataclass(frozen=True)
class ModalDictCache:
    modal_dict: modal.Dict

    async def get(self, key: str) -> object:
        if (result := await self.modal_dict.get.aio(key)) is None:
            raise KeyError(key)
        return result

    async def put(self, key: str, value: object) -> None:
        await self.modal_dict.put.aio(key, value)


@dataclass(frozen=True)
class StatsCache:
    STALE_AFTER_SECONDS: ClassVar[int] = 60
    DEFAULT_DAYS: ClassVar[int] = 7

    cache: Cache
    db: Database
    spawn: RefreshSpawner

    @staticmethod
    def key(days: int) -> str:
        return f"data:{days}"

    async def get(self, days: int) -> object:
        try:
            cached_at, cached = await self.cache.get(self.key(days))
        except KeyError:
            return await self.refresh(days)
        if time.time() - cached_at > self.STALE_AFTER_SECONDS:
            await self.spawn(days)
        return cached

    async def refresh(self, days: int) -> object:
        result = await self.db.query_all(days)
        payload = result.model_dump(mode="json")
        await self.cache.put(self.key(days), (time.time(), payload))
        return payload


@dataclass(frozen=True)
class MyStatCache:
    cache: Cache
    db: Database

    @staticmethod
    def key(contributor_id: str) -> str:
        return f"my-stat:{contributor_id}"

    async def get_or_compute(self, contributor_id: str) -> dict | None:
        try:
            wrapped = await self.cache.get(self.key(contributor_id))
        except KeyError:
            return await self.refresh(contributor_id)
        return wrapped["result"]

    async def refresh(self, contributor_id: str) -> dict | None:
        result = await self.db.query_my_stat(contributor_id)
        if result is None:
            return None
        payload = result.model_dump(mode="json")
        await self.cache.put(self.key(contributor_id), {"result": payload})
        return payload


@dataclass(frozen=True)
class ShareStore:
    cache: Cache

    @staticmethod
    def key(share_id: str) -> str:
        return f"share:{share_id}"

    async def get(self, share_id: str) -> dict | None:
        try:
            return await self.cache.get(self.key(share_id))
        except KeyError:
            return None

    async def put(self, record: ShareRecord) -> None:
        await self.cache.put(self.key(record.id), record.model_dump(mode="json"))


SHARE_REQUEST_MAX_AGE_SECONDS = 300
DASHBOARD_URL = "https://sentiments.cc"


def create_app(
    db: Database,
    verifier: Verifier,
    data_cache: Cache,
    share_cache: Cache,
    spawn: RefreshSpawner,
    spawn_my_stat: MyStatSpawner,
    revalidate: RevalidateSpawner,
    warm_share: ShareWarmSpawner,
    allowed_origins: list[str],
    data_api_token: str = "",
) -> FastAPI:
    limiter = Limiter(key_func=get_remote_address)
    stats_cache = StatsCache(data_cache, db, spawn)
    my_stat_cache = MyStatCache(data_cache, db)
    share_store = ShareStore(share_cache)

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
        if not await verifier.verify_signature(body.contributor_type, body.contributor_id, body.test_payload, body.signature):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)
        return StatusResponse()

    @web_app.post("/upload")
    @limiter.limit("1000/minute")
    async def upload(request: Request, payload: UploadPayload) -> UploadResponse:
        if not await verifier.verify_signature(
            payload.contributor_type,
            payload.contributor_id,
            json.dumps(
                [r.model_dump(mode="json") for r in payload.records],
                sort_keys=True,
                separators=(",", ":"),
            ),
            payload.signature,
        ):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)

        db_contributor_id = (
            payload.contributor_id.split("/", 1)[0]
            if payload.contributor_type == "gist"
            else payload.contributor_id
        )
        await db.ingest(payload.records, db_contributor_id, payload.contributor_type)
        await asyncio.gather(
            stats_cache.spawn(StatsCache.DEFAULT_DAYS),
            spawn_my_stat(db_contributor_id),
            revalidate("dashboard"),
            revalidate(f"user:{db_contributor_id}"),
        )
        return UploadResponse(ingested=len(payload.records))

    @web_app.post("/daemon-event")
    @limiter.limit("30/minute")
    async def daemon_event(request: Request, payload: DaemonEventPayload) -> StatusResponse:
        if not await verifier.verify_signature(
            payload.contributor_type,
            payload.contributor_id,
            json.dumps(
                payload.event.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            payload.signature,
        ):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)

        db_contributor_id = (
            payload.contributor_id.split("/", 1)[0]
            if payload.contributor_type == "gist"
            else payload.contributor_id
        )
        await db.record_daemon_event(payload.event, db_contributor_id, payload.contributor_type)
        return StatusResponse()

    @web_app.get("/my-stats")
    @limiter.limit("30/minute")
    async def my_stats(
        request: Request,
        contributor_id: str = Query(min_length=1),
    ) -> MyStatResponse:
        payload = await my_stat_cache.get_or_compute(contributor_id)
        if payload is None:
            return JSONResponse({"detail": "Not enough data yet"}, status_code=404)
        return JSONResponse(
            payload,
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @web_app.post("/share")
    @limiter.limit("10/minute")
    async def mint_share(request: Request, body: ShareMintRequest) -> ShareMintResponse:
        now = datetime.now(timezone.utc)
        age = abs((now - body.payload.issued_at).total_seconds())
        if age > SHARE_REQUEST_MAX_AGE_SECONDS:
            return JSONResponse({"detail": "Stale or future issued_at"}, status_code=400)

        if not await verifier.verify_signature(
            body.contributor_type,
            body.contributor_id,
            json.dumps(
                body.payload.model_dump(mode="json"),
                sort_keys=True,
                separators=(",", ":"),
            ),
            body.signature,
        ):
            return JSONResponse({"detail": "Signature verification failed"}, status_code=401)

        db_contributor_id = (
            body.contributor_id.split("/", 1)[0]
            if body.contributor_type == "gist"
            else body.contributor_id
        )
        avatar_url = await verifier.resolve_avatar_url(body.contributor_type, db_contributor_id)
        record = ShareRecord(
            id=secrets.token_urlsafe(6),
            contributor_type=body.contributor_type,
            contributor_id=db_contributor_id,
            avatar_url=avatar_url,
            created_at=now,
        )
        await share_store.put(record)
        await warm_share(record.id)
        return ShareMintResponse(id=record.id, url=f"{DASHBOARD_URL}/share/{record.id}")

    @web_app.get("/share/{share_id}")
    @limiter.limit("120/minute")
    async def get_share(
        request: Request,
        share_id: str = Path(min_length=1, max_length=64),
    ) -> ShareRecord:
        record = await share_store.get(share_id)
        if record is None:
            return JSONResponse({"detail": "Share not found"}, status_code=404)
        return JSONResponse(record, headers={"Cache-Control": "public, max-age=86400"})

    @web_app.get("/data")
    @limiter.limit("120/minute")
    async def data(
        request: Request,
        days: int = Query(default=StatsCache.DEFAULT_DAYS, ge=1, le=365),
        authorization: str = Header(),
    ) -> DataResponse:
        if not data_api_token or authorization != f"Bearer {data_api_token}":
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        payload = await stats_cache.get(days)
        return JSONResponse(payload, headers={"Cache-Control": "public, max-age=3600"})

    return web_app


app = modal.App("cc-sentiment")

image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install("psycopg[binary]", "psycopg_pool", "pydantic", "httpx", "fastapi[standard]", "slowapi", "python-gnupg")
    .apt_install("openssh-client", "gnupg")
    .add_local_python_source("cc_sentiment_server")
)


@app.cls(
    image=image,
    secrets=[modal.Secret.from_name("cc-sentiment-db")],
    scaledown_window=600,
    min_containers=1,
    enable_memory_snapshot=True,
)
@modal.concurrent(max_inputs=100)
class API:
    KEYS_DICT: ClassVar[modal.Dict] = modal.Dict.from_name("github-keys", create_if_missing=True)
    DATA_DICT: ClassVar[modal.Dict] = modal.Dict.from_name("data-cache", create_if_missing=True)
    SHARE_DICT: ClassVar[modal.Dict] = modal.Dict.from_name("share-records", create_if_missing=True)

    db: Database
    verifier: Verifier
    data_cache: ModalDictCache
    share_cache: ModalDictCache

    @modal.enter()
    async def startup(self) -> None:
        self.db = Database(os.environ["TIMESCALE_DSN"])
        await self.db.open()
        self.verifier = Verifier(key_cache=ModalKeyCache(self.KEYS_DICT))
        self.data_cache = ModalDictCache(self.DATA_DICT)
        self.share_cache = ModalDictCache(self.SHARE_DICT)
        await refresh_stats.spawn.aio(StatsCache.DEFAULT_DAYS)

    @modal.exit()
    async def shutdown(self) -> None:
        await self.db.close()

    @modal.asgi_app()
    def serve(self) -> Callable:
        async def spawn(days: int) -> None:
            await refresh_stats.spawn.aio(days)
        async def spawn_my_stat(contributor_id: str) -> None:
            await refresh_my_stat.spawn.aio(contributor_id)
        async def revalidate(tag: str) -> None:
            await revalidate_dashboard.spawn.aio(tag)
        async def warm_share(share_id: str) -> None:
            await warm_share_card.spawn.aio(share_id)
        return create_app(
            db=self.db,
            verifier=self.verifier,
            data_cache=self.data_cache,
            share_cache=self.share_cache,
            spawn=spawn,
            spawn_my_stat=spawn_my_stat,
            revalidate=revalidate,
            warm_share=warm_share,
            allowed_origins=os.environ["ALLOWED_ORIGINS"].split(","),
            data_api_token=os.environ["DATA_API_TOKEN"],
        )


@app.function(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")])
async def seed() -> None:
    db = Database(os.environ["TIMESCALE_DSN"])
    await db.open()
    try:
        await db.seed()
        print("Seed complete: table, hypertable, indexes, continuous aggregate, compression policy.")
    finally:
        await db.close()


@app.function(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")], enable_memory_snapshot=True)
@modal.batched(max_batch_size=100, wait_ms=5_000)
async def refresh_stats(days_list: list[int]) -> list[None]:
    db = Database(os.environ["TIMESCALE_DSN"])
    await db.open()
    try:
        cache = StatsCache(ModalDictCache(API.DATA_DICT), db, noop)
        await asyncio.gather(*(cache.refresh(d) for d in sorted(set(days_list))))
    finally:
        await db.close()
    return [None] * len(days_list)


@app.function(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")], enable_memory_snapshot=True)
@modal.batched(max_batch_size=100, wait_ms=5_000)
async def refresh_my_stat(contributor_ids: list[str]) -> list[None]:
    db = Database(os.environ["TIMESCALE_DSN"])
    await db.open()
    try:
        cache = MyStatCache(ModalDictCache(API.DATA_DICT), db)
        await asyncio.gather(*(cache.refresh(c) for c in sorted(set(contributor_ids))))
    finally:
        await db.close()
    return [None] * len(contributor_ids)


@app.function(image=image, enable_memory_snapshot=True)
async def warm_share_card(share_id: str) -> None:
    headers = {"User-Agent": "Twitterbot/1.0", "Accept": "*/*"}
    async with httpx.AsyncClient(headers=headers, timeout=15.0) as client:
        page, og = await asyncio.gather(
            client.get(f"{DASHBOARD_URL}/share/{share_id}"),
            client.get(f"{DASHBOARD_URL}/share/{share_id}/og"),
            return_exceptions=True,
        )
    print(f"warm_share id={share_id!r} page={getattr(page, 'status_code', page)!r} og={getattr(og, 'status_code', og)!r}")


@app.function(image=image, secrets=[modal.Secret.from_name("cc-sentiment-vercel")], enable_memory_snapshot=True)
@modal.batched(max_batch_size=100, wait_ms=5_000)
async def revalidate_dashboard(tags: list[str]) -> list[bool]:
    unique = sorted(set(tags))
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            "https://api.vercel.com/v1/edge-cache/invalidate-by-tags",
            params={
                "projectIdOrName": os.environ["VERCEL_PROJECT_ID"],
                "teamId": os.environ["VERCEL_TEAM_ID"],
            },
            headers={"Authorization": f"Bearer {os.environ['VERCEL_API_TOKEN']}"},
            json={"tags": unique, "target": "production"},
        )
        response.raise_for_status()
    return [True] * len(tags)


@app.local_entrypoint()
async def run_seed() -> None:
    await seed.remote.aio()
