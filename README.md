# cc-sentiment

Capture developer sentiment towards Claude Code over time to detect performance regressions from user mood patterns.

Motivated by [claude-code#42796](https://github.com/anthropics/claude-code/issues/42796) — when Claude Code regresses, developers get frustrated. We want to measure that frustration signal directly, independent of any internal telemetry, and surface it as a public dashboard.

## Architecture

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   client/   │  POST   │   server/   │  API    │    app/     │
│  macOS CLI  │────────▶│  Modal API  │◀────────│   Svelte    │
│  MLX+Gemma4 │ signed  │  timeseries │  cached │  dashboard  │
└─────────────┘ upload  └─────────────┘  queries└─────────────┘
```

**`client/`** — macOS Apple Silicon CLI. Discovers Claude Code conversation transcripts in `~/.claude/`, runs them through Gemma 4 locally via MLX for sentiment scoring, signs results with the developer's GitHub SSH key, and uploads to the server.

**`server/`** — Python 3.14 backend on Modal. Accepts signed sentiment uploads, verifies GitHub SSH signatures, stores timeseries data. Exposes data query APIs for the frontend.

**`app/`** — Svelte frontend. Consumes server data APIs and renders sentiment charts (by time of day, by day of week, rolling averages). Heavily cached — prioritizes fast loads over real-time updates.

## How It Works

1. Developer uses Claude Code normally
2. Client CLI discovers new transcripts in `~/.claude/projects/<slug>/<uuid>.jsonl`
3. Gemma 4 (local, on Apple Silicon via MLX) scores each conversation's sentiment (1-5 Likert)
4. Results are signed with the developer's GitHub SSH key
5. Server verifies signatures against `github.com/<username>.keys`, stores in timeseries DB
6. App queries server APIs and renders cached dashboard charts

## Open Questions

### Server
- **Timeseries DB**: SQLite on a Modal Volume (simplest) vs InfluxDB/TimescaleDB/QuestDB (better for time-range queries). Start with SQLite, migrate if needed.
- **Caching strategy**: How aggressively to cache API responses. New data arrives in batches (when developers run the client), not continuously.

### App
- **Chart library**: Chart.js, D3, or Layerchart (Svelte-native). Trade off interactivity vs simplicity.
- **SSR vs SPA**: SvelteKit with prerendering for maximum cacheability, or pure SPA hitting cached API responses.

### Client
- **Gemma 4 model variant**: `mlx-community/gemma-4-e4b-it-4bit` vs `unsloth/gemma-4-E4B-it-UD-MLX-4bit`. Need to benchmark quality and speed on M-series chips.
- **Sentiment prompt design**: The classifier prompt is critical — it shifts the entire dataset. Must be versioned and included in uploaded records.
- **Transcript parsing**: Claude Code JSONL includes tool calls, errors, user messages. Which signals matter? User messages are primary; error frequency and assistant apologies are secondary.

## API

### `POST /upload`
Signed payload with sentiment scores. Server verifies GitHub SSH signature before ingesting.

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
      "model_id": "gemma-4-e4b-it-4bit",
      "client_version": "0.1.0"
    }
  ]
}
```

### `GET /data`
Query timeseries data for the dashboard. Supports time range, aggregation interval, and grouping dimensions. Response is heavily cached.

## Development

See `AGENTS.md` for shared conventions. Each component has its own:
- `server/AGENTS.md` — Python style, Modal patterns, API design
- `app/AGENTS.md` — Svelte, charting, caching
- `client/AGENTS.md` — macOS CLI, MLX inference, SSH signing
