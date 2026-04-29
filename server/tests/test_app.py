from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from cc_sentiment_server.app import DictCache, create_app
from cc_sentiment_server.db import Database
from cc_sentiment_server.models import SentimentRecord


VALID_RECORD: dict = {
    "time": "2026-04-12T10:30:00Z",
    "conversation_id": "abc-123",
    "bucket_index": 0,
    "sentiment_score": 4,
    "prompt_version": "v1",
    "claude_model": "claude-sonnet-4-20250514",
    "client_version": "0.1.0",
    "read_edit_ratio": None,
    "edits_without_prior_read_ratio": None,
    "write_edit_ratio": None,
    "tool_calls_per_turn": 0.0,
    "subagent_count": 0,
    "turn_count": 1,
    "thinking_present": False,
    "thinking_chars": 0,
    "cc_version": "2.1.92",
}

VALID_PAYLOAD: dict = {
    "contributor_type": "github",
    "contributor_id": "octocat",
    "signature": "sig-content",
    "records": [VALID_RECORD],
}

AUTH_HEADER: dict = {"Authorization": "Bearer test-token"}


@pytest.fixture
def verifier() -> AsyncMock:
    v = AsyncMock()
    v.verify_signature.return_value = True
    v.resolve_avatar_url.return_value = "https://github.com/octocat.png?size=400"
    v.parse_gist_id = MagicMock(side_effect=lambda combined: tuple(combined.split("/", 1)))
    return v


@pytest.fixture
def warm_share_spawn_mock() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
async def client(
    db: Database,
    verifier: AsyncMock,
    warm_share_spawn_mock: AsyncMock,
) -> httpx.AsyncClient:
    async def noop_spawn(days: int) -> None:
        pass
    async def noop_spawn_my_stat(contributor_id: str) -> None:
        pass
    async def noop_revalidate(tag: str) -> None:
        pass
    app = create_app(
        db=db,
        verifier=verifier,
        data_cache=DictCache(),
        share_cache=DictCache(),
        spawn=noop_spawn,
        spawn_my_stat=noop_spawn_my_stat,
        revalidate=noop_revalidate,
        spawn_warm_share=warm_share_spawn_mock,
        allowed_origins=["http://localhost:3000"],
        data_api_token="test-token",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


class TestUpload:
    @pytest.mark.asyncio
    async def test_valid_payload(self, client: httpx.AsyncClient, db: Database) -> None:
        response = await client.post("/upload", json=VALID_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"status": "ok", "ingested": 1}

        async with db.pool.connection() as conn:
            row = await (await conn.execute("SELECT count(*) FROM sentiment")).fetchone()
        assert row[0] == 1

    @pytest.mark.asyncio
    async def test_invalid_signature(self, client: httpx.AsyncClient, verifier: AsyncMock) -> None:
        verifier.verify_signature.return_value = False

        response = await client.post("/upload", json=VALID_PAYLOAD)

        assert response.status_code == 401
        assert "Signature verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_malformed_json(self, client: httpx.AsyncClient) -> None:
        response = await client.post(
            "/upload",
            content=b"not json{{{",
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_missing_required_fields(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/upload", json={"contributor_type": "github",
            "contributor_id": "octocat"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_sentiment_score_out_of_range(self, client: httpx.AsyncClient) -> None:
        bad = {**VALID_RECORD, "sentiment_score": 6}
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": [bad]})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_sentiment_score_zero(self, client: httpx.AsyncClient) -> None:
        bad = {**VALID_RECORD, "sentiment_score": 0}
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": [bad]})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_negative_bucket_index(self, client: httpx.AsyncClient) -> None:
        bad = {**VALID_RECORD, "bucket_index": -1}
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": [bad]})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_records_list(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": []})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_conversation_id(self, client: httpx.AsyncClient) -> None:
        bad = {**VALID_RECORD, "conversation_id": ""}
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": [bad]})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_multiple_records(self, client: httpx.AsyncClient, db: Database) -> None:
        records = [
            {**VALID_RECORD, "bucket_index": i, "sentiment_score": min(i + 1, 5)}
            for i in range(5)
        ]
        response = await client.post("/upload", json={**VALID_PAYLOAD, "records": records})

        assert response.status_code == 200
        assert response.json()["ingested"] == 5

        async with db.pool.connection() as conn:
            row = await (await conn.execute("SELECT count(*) FROM sentiment")).fetchone()
        assert row[0] == 5

    @pytest.mark.asyncio
    async def test_upload_then_query(self, client: httpx.AsyncClient) -> None:
        now = datetime.now(timezone.utc).isoformat()
        record = {**VALID_RECORD, "time": now}
        await client.post("/upload", json={**VALID_PAYLOAD, "records": [record]})

        response = await client.get("/data", headers=AUTH_HEADER)
        assert response.status_code == 200
        assert response.json()["total_records"] == 1

    @pytest.mark.asyncio
    async def test_gist_upload_stores_normalized_contributor_id(
        self, client: httpx.AsyncClient, db: Database
    ) -> None:
        payload = {
            **VALID_PAYLOAD,
            "contributor_type": "gist",
            "contributor_id": "octocat/abcdef1234567890abcd",
        }
        response = await client.post("/upload", json=payload)

        assert response.status_code == 200

        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT contributor_id, contributor_type FROM sentiment"
            )).fetchone()

        assert row[0] == "octocat"
        assert row[1] == "gist"


class TestVerifyEndpoint:
    @pytest.mark.asyncio
    async def test_valid_signature(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/verify", json={
            "contributor_type": "github",
            "contributor_id": "octocat",
            "signature": "sig-content",
            "test_payload": "cc-sentiment-verify",
        })

        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    @pytest.mark.asyncio
    async def test_invalid_signature(self, client: httpx.AsyncClient, verifier: AsyncMock) -> None:
        verifier.verify_signature.return_value = False

        response = await client.post("/verify", json={
            "contributor_type": "github",
            "contributor_id": "octocat",
            "signature": "bad-sig",
            "test_payload": "cc-sentiment-verify",
        })

        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_missing_fields(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/verify", json={
            "contributor_type": "github",
            "contributor_id": "octocat",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_empty_contributor_id(self, client: httpx.AsyncClient) -> None:
        response = await client.post("/verify", json={
            "contributor_type": "github",
            "contributor_id": "",
            "signature": "sig",
            "test_payload": "payload",
        })
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_value_error_returns_400(self, client: httpx.AsyncClient, verifier: AsyncMock) -> None:
        verifier.verify_signature.side_effect = ValueError("Invalid GitHub username")

        response = await client.post("/verify", json={
            "contributor_type": "github",
            "contributor_id": "octocat",
            "signature": "sig",
            "test_payload": "payload",
        })

        assert response.status_code == 400
        assert "Invalid GitHub username" in response.json()["detail"]


VALID_DAEMON_EVENT: dict = {
    "event_type": "install",
    "client_version": "0.1.0",
    "time": "2026-04-18T10:30:00Z",
}

VALID_DAEMON_PAYLOAD: dict = {
    "contributor_type": "github",
    "contributor_id": "octocat",
    "signature": "sig-content",
    "event": VALID_DAEMON_EVENT,
}


class TestDaemonEvent:
    @pytest.mark.asyncio
    async def test_install_event(self, client: httpx.AsyncClient, db: Database) -> None:
        response = await client.post("/daemon-event", json=VALID_DAEMON_PAYLOAD)

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT contributor_id, contributor_type, event_type, client_version FROM daemon_events"
            )).fetchone()
        assert row == ("octocat", "github", "install", "0.1.0")

    @pytest.mark.asyncio
    async def test_uninstall_event(self, client: httpx.AsyncClient, db: Database) -> None:
        payload = {
            **VALID_DAEMON_PAYLOAD,
            "event": {**VALID_DAEMON_EVENT, "event_type": "uninstall"},
        }
        response = await client.post("/daemon-event", json=payload)

        assert response.status_code == 200
        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT event_type FROM daemon_events"
            )).fetchone()
        assert row[0] == "uninstall"

    @pytest.mark.asyncio
    async def test_invalid_signature(self, client: httpx.AsyncClient, verifier: AsyncMock) -> None:
        verifier.verify_signature.return_value = False
        response = await client.post("/daemon-event", json=VALID_DAEMON_PAYLOAD)
        assert response.status_code == 401

    @pytest.mark.asyncio
    async def test_invalid_event_type(self, client: httpx.AsyncClient) -> None:
        payload = {
            **VALID_DAEMON_PAYLOAD,
            "event": {**VALID_DAEMON_EVENT, "event_type": "bogus"},
        }
        response = await client.post("/daemon-event", json=payload)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_gist_stores_normalized_contributor_id(
        self, client: httpx.AsyncClient, db: Database
    ) -> None:
        payload = {
            **VALID_DAEMON_PAYLOAD,
            "contributor_type": "gist",
            "contributor_id": "octocat/abcdef1234567890abcd",
        }
        response = await client.post("/daemon-event", json=payload)

        assert response.status_code == 200
        async with db.pool.connection() as conn:
            row = await (await conn.execute(
                "SELECT contributor_id, contributor_type FROM daemon_events"
            )).fetchone()
        assert row == ("octocat", "gist")


class TestData:
    @pytest.mark.asyncio
    async def test_returns_correct_shape(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/data", headers=AUTH_HEADER)

        assert response.status_code == 200
        body = response.json()
        for key in ("timeline", "distribution", "total_records", "total_sessions", "last_updated"):
            assert key in body

    @pytest.mark.asyncio
    async def test_rejects_missing_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/data")).status_code == 422

    @pytest.mark.asyncio
    async def test_rejects_bad_token(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/data", headers={"Authorization": "Bearer wrong"})).status_code == 401

    @pytest.mark.asyncio
    async def test_cache_control_header(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/data", headers=AUTH_HEADER)
        assert response.headers["cache-control"] == "public, max-age=3600"

    @pytest.mark.asyncio
    async def test_days_param(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/data?days=30", headers=AUTH_HEADER)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_days_too_low(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/data?days=0", headers=AUTH_HEADER)).status_code == 422

    @pytest.mark.asyncio
    async def test_days_too_high(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/data?days=999", headers=AUTH_HEADER)).status_code == 422

    @pytest.mark.asyncio
    async def test_days_not_integer(self, client: httpx.AsyncClient) -> None:
        assert (await client.get("/data?days=abc", headers=AUTH_HEADER)).status_code == 422

    @pytest.mark.asyncio
    async def test_caches_response(self, client: httpx.AsyncClient, db: Database) -> None:
        now = datetime.now(timezone.utc)
        records = [SentimentRecord(
            time=now, conversation_id="c1", bucket_index=0,
            sentiment_score=3, prompt_version="v1",
            claude_model="claude-sonnet-4-20250514", client_version="0.1.0",
            read_edit_ratio=None, edits_without_prior_read_ratio=None,
            write_edit_ratio=None, tool_calls_per_turn=0.0, subagent_count=0,
            turn_count=1,
            thinking_present=False, thinking_chars=0, cc_version="2.1.92",
        )]
        await db.ingest(records, "octocat", "github")

        r1 = await client.get("/data", headers=AUTH_HEADER)
        assert r1.json()["total_records"] == 1

        await db.ingest([SentimentRecord(
            time=now, conversation_id="c2", bucket_index=0,
            sentiment_score=4, prompt_version="v1",
            claude_model="claude-sonnet-4-20250514", client_version="0.1.0",
            read_edit_ratio=None, edits_without_prior_read_ratio=None,
            write_edit_ratio=None, tool_calls_per_turn=0.0, subagent_count=0,
            turn_count=1,
            thinking_present=False, thinking_chars=0, cc_version="2.1.92",
        )], "octocat", "github")

        r2 = await client.get("/data", headers=AUTH_HEADER)
        assert r2.json()["total_records"] == 1  # still cached


def share_payload(**overrides: object) -> dict:
    issued_at = overrides.pop("issued_at", datetime.now(timezone.utc).isoformat())
    return {
        "contributor_type": overrides.pop("contributor_type", "github"),
        "contributor_id": overrides.pop("contributor_id", "octocat"),
        "signature": overrides.pop("signature", "sig-content"),
        "payload": {"issued_at": issued_at},
        **overrides,
    }


class TestShare:
    @pytest.mark.asyncio
    async def test_mint_happy_path(
        self, client: httpx.AsyncClient, warm_share_spawn_mock: AsyncMock
    ) -> None:
        response = await client.post("/share", json=share_payload())

        assert response.status_code == 200
        body = response.json()
        assert set(body.keys()) == {"id", "url"}
        assert body["id"]
        assert body["url"] == f"https://sentiments.cc/share/{body['id']}"
        warm_share_spawn_mock.assert_awaited_once_with(body["id"])

    @pytest.mark.asyncio
    async def test_mint_invalid_signature(
        self, client: httpx.AsyncClient, verifier: AsyncMock
    ) -> None:
        verifier.verify_signature.return_value = False

        response = await client.post("/share", json=share_payload())

        assert response.status_code == 401
        assert "Signature verification failed" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_mint_stale_issued_at(self, client: httpx.AsyncClient) -> None:
        stale = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        response = await client.post("/share", json=share_payload(issued_at=stale))

        assert response.status_code == 400
        assert "Stale" in response.json()["detail"]

    @pytest.mark.asyncio
    async def test_mint_future_issued_at(self, client: httpx.AsyncClient) -> None:
        future = (datetime.now(timezone.utc) + timedelta(seconds=600)).isoformat()
        response = await client.post("/share", json=share_payload(issued_at=future))

        assert response.status_code == 400

    @pytest.mark.asyncio
    async def test_mint_gist_normalizes_contributor(
        self, client: httpx.AsyncClient
    ) -> None:
        response = await client.post("/share", json=share_payload(
            contributor_type="gist",
            contributor_id="octocat/abcdef1234567890abcd",
        ))
        assert response.status_code == 200

        share_id = response.json()["id"]
        fetched = await client.get(f"/share/{share_id}")
        assert fetched.status_code == 200
        record = fetched.json()
        assert record["contributor_type"] == "gist"
        assert record["contributor_id"] == "octocat"

    @pytest.mark.asyncio
    async def test_mint_gpg_with_gravatar(
        self, client: httpx.AsyncClient, verifier: AsyncMock
    ) -> None:
        gravatar = "https://www.gravatar.com/avatar/aabbccddeeff?s=400&d=404"
        verifier.resolve_avatar_url.return_value = gravatar

        response = await client.post("/share", json=share_payload(
            contributor_type="gpg",
            contributor_id="ABCDEF0123456789",
        ))
        assert response.status_code == 200

        fetched = await client.get(f"/share/{response.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["avatar_url"] == gravatar

    @pytest.mark.asyncio
    async def test_mint_gpg_without_gravatar(
        self, client: httpx.AsyncClient, verifier: AsyncMock
    ) -> None:
        verifier.resolve_avatar_url.return_value = None

        response = await client.post("/share", json=share_payload(
            contributor_type="gpg",
            contributor_id="ABCDEF0123456789",
        ))
        assert response.status_code == 200

        fetched = await client.get(f"/share/{response.json()['id']}")
        assert fetched.status_code == 200
        assert fetched.json()["avatar_url"] is None

    @pytest.mark.asyncio
    async def test_get_share_happy_path(self, client: httpx.AsyncClient) -> None:
        mint = await client.post("/share", json=share_payload())
        share_id = mint.json()["id"]

        response = await client.get(f"/share/{share_id}")

        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=86400"
        record = response.json()
        assert record["id"] == share_id
        assert record["contributor_type"] == "github"
        assert record["contributor_id"] == "octocat"
        assert record["avatar_url"] == "https://github.com/octocat.png?size=400"
        assert record["created_at"]

    @pytest.mark.asyncio
    async def test_get_share_not_found(self, client: httpx.AsyncClient) -> None:
        response = await client.get("/share/does-not-exist")

        assert response.status_code == 404
        assert "Share not found" in response.json()["detail"]
