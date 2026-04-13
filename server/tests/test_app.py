from __future__ import annotations

import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

modal_mock = MagicMock()
modal_mock.App.return_value.cls.return_value = lambda cls: cls
sys.modules.setdefault("modal", modal_mock)

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app import API
from models import DataResponse


VALID_RECORD: dict = {
    "time": "2026-04-12T10:30:00Z",
    "conversation_id": "abc-123",
    "bucket_index": 0,
    "sentiment_score": 4,
    "prompt_version": "v1",
    "model_id": "gemma-4-e4b-it-4bit",
    "client_version": "0.1.0",
}

VALID_PAYLOAD: dict = {
    "github_username": "octocat",
    "signature": "sig-content",
    "records": [VALID_RECORD],
}


def make_empty_data_response() -> DataResponse:
    return DataResponse(
        timeline=[],
        hourly=[],
        weekday=[],
        distribution=[],
        total_records=0,
        last_updated=datetime(2026, 4, 12, 12, 0, tzinfo=timezone.utc),
    )


def make_test_client(db: MagicMock | None = None, verifier: MagicMock | None = None) -> TestClient:
    api = API()
    api.db = db or MagicMock()
    api.verifier = verifier or MagicMock()
    routes = [
        Route("/verify", api.handle_verify, methods=["POST"]),
        Route("/upload", api.handle_upload, methods=["POST"]),
        Route("/data", api.handle_data, methods=["GET"]),
    ]
    return TestClient(Starlette(routes=routes))


class TestUpload:
    def test_valid_payload_returns_200(self) -> None:
        verifier = MagicMock()
        verifier.verify_signature.return_value = True
        db = MagicMock()
        client = make_test_client(db=db, verifier=verifier)

        response = client.post("/upload", json=VALID_PAYLOAD)

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["ingested"] == 1
        db.ingest.assert_called_once()

    def test_invalid_signature_returns_401(self) -> None:
        verifier = MagicMock()
        verifier.verify_signature.return_value = False
        client = make_test_client(verifier=verifier)

        response = client.post("/upload", json=VALID_PAYLOAD)

        assert response.status_code == 401
        assert "Signature verification failed" in response.json()["error"]

    def test_malformed_json_returns_400(self) -> None:
        client = make_test_client()

        response = client.post(
            "/upload",
            content=b"not json{{{",
            headers={"content-type": "application/json"},
        )

        assert response.status_code == 400
        assert "Invalid JSON" in response.json()["error"]

    def test_invalid_pydantic_payload_returns_400(self) -> None:
        client = make_test_client()

        response = client.post("/upload", json={"github_username": "octocat"})

        assert response.status_code == 400

    def test_invalid_username_returns_400(self) -> None:
        verifier = MagicMock()
        verifier.verify_signature.side_effect = ValueError("Invalid GitHub username: 'bad\\nuser'")
        client = make_test_client(verifier=verifier)

        payload = {**VALID_PAYLOAD, "github_username": "bad\nuser"}
        response = client.post("/upload", json=payload)

        assert response.status_code == 400
        assert "Invalid GitHub username" in response.json()["error"]


class TestVerify:
    def test_valid_signature_returns_200(self) -> None:
        verifier = MagicMock()
        verifier.verify_signature.return_value = True
        client = make_test_client(verifier=verifier)

        response = client.post("/verify", json={
            "github_username": "octocat",
            "signature": "sig-content",
            "test_payload": "cc-sentiment-verify",
        })

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_invalid_signature_returns_401(self) -> None:
        verifier = MagicMock()
        verifier.verify_signature.return_value = False
        client = make_test_client(verifier=verifier)

        response = client.post("/verify", json={
            "github_username": "octocat",
            "signature": "bad-sig",
            "test_payload": "cc-sentiment-verify",
        })

        assert response.status_code == 401

    def test_missing_fields_returns_400(self) -> None:
        client = make_test_client()

        response = client.post("/verify", json={"github_username": "octocat"})

        assert response.status_code == 400


class TestData:
    def test_returns_correct_shape(self) -> None:
        db = MagicMock()
        db.query_all.return_value = make_empty_data_response()
        client = make_test_client(db=db)

        response = client.get("/data")

        assert response.status_code == 200
        data = response.json()
        assert "timeline" in data
        assert "hourly" in data
        assert "weekday" in data
        assert "distribution" in data
        assert "total_records" in data
        assert "last_updated" in data

    def test_includes_cache_control_header(self) -> None:
        db = MagicMock()
        db.query_all.return_value = make_empty_data_response()
        client = make_test_client(db=db)

        response = client.get("/data")

        assert response.headers["cache-control"] == "public, max-age=3600"

    def test_days_param(self) -> None:
        db = MagicMock()
        db.query_all.return_value = make_empty_data_response()
        client = make_test_client(db=db)

        response = client.get("/data?days=30")

        assert response.status_code == 200
        db.query_all.assert_called_once_with(30)
