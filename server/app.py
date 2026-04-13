from __future__ import annotations

import asyncio
import json
import os

import modal
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from db import Database
from models import UploadPayload
from verify import Verifier

__all__ = ["app"]

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

    async def handle_verify(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        username = body.get("github_username")
        signature = body.get("signature")
        test_payload = body.get("test_payload")
        if not all([username, signature, test_payload]):
            return JSONResponse(
                {"error": "Missing github_username, signature, or test_payload"},
                status_code=400,
            )

        try:
            verified = await asyncio.to_thread(
                self.verifier.verify_signature, username, test_payload, signature,
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        if not verified:
            return JSONResponse(
                {"error": "Signature verification failed"}, status_code=401,
            )
        return JSONResponse({"status": "ok"})

    async def handle_upload(self, request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        try:
            payload = UploadPayload.model_validate(body)
        except ValidationError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        canonical = json.dumps(
            [r.model_dump(mode="json") for r in payload.records],
            sort_keys=True,
            separators=(",", ":"),
        )

        try:
            verified = await asyncio.to_thread(
                self.verifier.verify_signature,
                payload.github_username,
                canonical,
                payload.signature,
            )
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)

        if not verified:
            return JSONResponse(
                {"error": "Signature verification failed"}, status_code=401,
            )

        await asyncio.to_thread(
            self.db.ingest, payload.records, payload.github_username,
        )
        return JSONResponse({"status": "ok", "ingested": len(payload.records)})

    async def handle_data(self, request: Request) -> JSONResponse:
        days = int(request.query_params["days"]) if "days" in request.query_params else 7
        response = await asyncio.to_thread(self.db.query_all, days)
        return JSONResponse(
            response.model_dump(mode="json"),
            headers={"Cache-Control": "public, max-age=3600"},
        )

    @modal.asgi_app()
    def serve(self) -> Starlette:
        allowed_origins = os.environ["ALLOWED_ORIGINS"].split(",")
        routes = [
            Route("/verify", self.handle_verify, methods=["POST"]),
            Route("/upload", self.handle_upload, methods=["POST"]),
            Route("/data", self.handle_data, methods=["GET"]),
        ]
        web_app = Starlette(routes=routes)
        web_app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type"],
        )
        return web_app
