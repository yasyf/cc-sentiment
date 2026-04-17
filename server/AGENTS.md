# server/ — Modal API

Python 3.14 backend deployed on Modal. Accepts signed sentiment uploads, verifies GitHub SSH signatures, stores timeseries data, and exposes query APIs for the dashboard app.

## Tech Stack

- **Runtime**: Python 3.14
- **Deployment**: Modal (serverless)
- **HTTP**: Modal web endpoints (`@modal.web_endpoint()` / `@modal.asgi_app()`)
- **Timeseries storage**: TBD — starting with SQLite on a Modal Volume
- **Signature verification**: `ssh-keygen -Y verify` via subprocess, GitHub API for public key lookup
- **Models**: Pydantic for all API boundaries

## Commands

```bash
uv sync                            # Install dependencies
uv run modal serve server/app.py   # Dev server (hot reload)
uv run modal deploy server/app.py  # Production deploy
uv run pytest server/              # Run tests
```

## Directory Structure (planned)

```
server/
├── pyproject.toml
├── app.py              # Modal app definition, web endpoints
├── db.py               # Timeseries storage layer
├── verify.py           # GitHub SSH signature verification
├── models.py           # Pydantic request/response models
└── tests/
    ├── test_verify.py
    └── test_db.py
```

## Modal Patterns

### App Structure

One `modal.App` per deployable unit. Classes use `@modal.cls()` for stateful services (DB connections, cached state). Functions use `@modal.function()` for stateless operations.

```python
app = modal.App("cc-sentiment")
volume = modal.Volume.from_name("cc-sentiment-data", create_if_missing=True)
image = modal.Image.debian_slim(python_version="3.14").pip_install("pydantic")
```

### Class Lifecycle

`@modal.build()` for image-time setup (install system deps, download static assets). `@modal.enter()` for container-start setup (open DB connections, load cached state). These replace `__init__` for Modal classes.

```python
@app.cls(image=image, volumes={"/data": volume})
class API:

    @modal.enter()
    def startup(self):
        self.db = open_db("/data/sentiment.db")

    @modal.web_endpoint(method="POST")
    def upload(self, payload: UploadPayload):
        verify_signature(payload)
        self.db.ingest(payload.records)

    @modal.web_endpoint(method="GET")
    def data(self, start: str, end: str, interval: str):
        return self.db.query(start, end, interval)
```

### Web Endpoints

Modal web endpoints receive the raw `starlette.requests.Request` or use Pydantic models for typed input. Return `JSONResponse` for API responses. CORS must be configured for the Svelte app origin.

## API Design

### `POST /upload`

Accepts a JSON payload:
```json
{
  "github_username": "octocat",
  "signature": "<ssh-sig base64>",
  "records": [
    {
      "timestamp": "2026-04-12T10:30:00Z",
      "conversation_id": "uuid",
      "sentiment_score": 4,
      "prompt_version": "v1",
      "claude_model": "claude-sonnet-4-20250514",
      "client_version": "0.1.0"
    }
  ]
}
```

Verification flow:
1. Fetch user's SSH keys from `https://github.com/<username>.keys`
2. Verify signature over the canonical JSON of `records` using `ssh-keygen -Y verify`
3. Reject if no key matches

### `GET /data`

Query parameters: `start`, `end`, `interval` (e.g. `1h`, `1d`), `group_by` (e.g. `hour_of_day`, `day_of_week`).

Returns aggregated sentiment data as JSON. Responses should be cached aggressively — data only changes when new uploads arrive.

## Style Specifics

All rules from root `AGENTS.md` apply, plus:

- **Pydantic models for all API boundaries.** Frozen models (`model_config = ConfigDict(frozen=True)`) for immutable data.
- **No ORM.** Raw SQL for the timeseries DB. The schema is trivial.
- **Signature verification is its own module.** Isolated, heavily tested. Subprocess calls to `ssh-keygen` with strict argument validation — never `shell=True`.
- **Modal volumes for persistence.** All persistent state on a Modal Volume at `/data`. No local filesystem assumptions.
- **CORS configuration.** The Svelte app needs cross-origin access. Configure allowed origins explicitly, not `*`.
- **Never call Modal functions directly from a FastAPI handler.** `create_app(...)` takes spawner Protocols (`RefreshSpawner`, `MyStatSpawner`, `RevalidateSpawner`) for every Modal side effect — `API.serve` wires them to `<fn>.spawn.aio(...)`, tests wire them to noops. Importing a Modal function symbol into the handler module and calling `.spawn.aio(...)` inline makes the request path depend on the Modal control plane and hang in tests. If you add a new Modal function that the request path triggers, add a new Protocol and a matching `create_app` parameter.

## Testing

- **Postgres image must be `timescale/timescaledb-ha:pg17-all`.** Do not use `timescale/timescaledb:*` or any `*-oss` tag. `Database.seed()` installs `timescaledb_toolkit` (required by the `hyperloglog`/`distinct_count`/`rollup` usage in the totals continuous aggregate and lifetime stats query); toolkit is not in the pg17 OSS bundle as of 2026. Switching to an OSS tag makes every DB-backed test ERROR at fixture setup and the failure mode is noisy-but-silent (pool connect warnings, not a clean "extension not available" trace).
- **Mock every Modal spawner with a noop.** Tests construct the app via `create_app(...)` with `async def noop(...): pass` for `spawn`, `spawn_my_stat`, and `revalidate`. No real `spawn.aio(...)` call should run in the test event loop.
- **Keep the session-scoped DB container.** `_seeded_db` is session-scoped; `db` is function-scoped and truncates `sentiment` between tests. Do not introduce autouse fixtures that branch on `request.fixturenames`, and do not try to session-scope the `client` fixture — the mock `verifier` is mutated in-place by some tests and needs the fresh per-test reset that function scope gives.
