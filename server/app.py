from __future__ import annotations

import json
import os

import modal
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from db import Database
from models import UploadPayload
from verify import Verifier

app = modal.App("cc-sentiment")

image = (
    modal.Image.debian_slim(python_version="3.14")
    .pip_install("psycopg[binary]", "pydantic", "httpx", "starlette")
    .apt_install("openssh-client")
)


@app.cls(image=image, secrets=[modal.Secret.from_name("cc-sentiment-db")])
class API:
    db: Database
    verifier: Verifier

    @modal.enter()
    def startup(self) -> None:
        self.db = Database(os.environ["TIMESCALE_DSN"])
        self.db.create_tables()
        self.verifier = Verifier()

    async def handle_upload(self, request: Request) -> JSONResponse:
        body = await request.json()
        payload = UploadPayload.model_validate(body)

        canonical = json.dumps(
            [r.model_dump(mode="json") for r in payload.records],
            sort_keys=True,
            separators=(",", ":"),
        )
        assert self.verifier.verify_signature(
            payload.github_username, canonical, payload.signature
        ), "Signature verification failed"

        self.db.ingest(payload.records, payload.github_username)
        return JSONResponse({"status": "ok", "ingested": len(payload.records)})

    async def handle_data(self, request: Request) -> JSONResponse:
        days = int(request.query_params.get("days", "7"))
        response = self.db.query_all(days=days)
        return JSONResponse(
            response.model_dump(mode="json"),
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @modal.asgi_app()
    def serve(self) -> Starlette:
        routes = [
            Route("/upload", self.handle_upload, methods=["POST"]),
            Route("/data", self.handle_data, methods=["GET"]),
        ]
        web_app = Starlette(routes=routes)
        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )
        return web_app
