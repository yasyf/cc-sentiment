# cc-sentiment

An open experiment in Claude Code sentiment -- does it vary by time of day, day of week, or model?

Contributors run a local CLI that scores their Claude Code transcripts on-device with Gemma 4 via MLX, then uploads only the numeric scores (not the conversations) to a shared dashboard.

**Dashboard**: [sentiments.cc](https://sentiments.cc)

## Quick Start

```bash
# Run it once without installing
uvx cc-sentiment

# Or install permanently
uv tool install cc-sentiment
cc-sentiment
```

The bare command walks you through setup (linking your GitHub account so uploads are attributable), scores your transcripts, and uploads the scores.

Requires macOS with Apple Silicon (for local Gemma 4 inference via MLX) and [uv](https://docs.astral.sh/uv/).

## How It Works

1. The CLI discovers Claude Code transcripts in `~/.claude/projects/`
2. Each conversation is split into time buckets and scored 1-5 locally using Gemma 4 on MLX
3. Each upload is tied to your GitHub account so uploads are attributable
4. The server verifies each upload against your GitHub-linked keys and stores the scores
5. The dashboard aggregates contributions so anyone can explore whether sentiment varies by time of day, day of week, or Claude model

**Your conversations never leave your machine.** Only numeric scores and timestamps are uploaded.

## Architecture

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   client/   │  POST   │   server/   │  fetch  │    app/     │
│  macOS CLI  │────────▶│  Modal API  │◀────────│  SvelteKit  │
│  MLX+Gemma4 │ signed  │ TimescaleDB │  SSR    │  dashboard  │
└─────────────┘ upload  └─────────────┘         └─────────────┘
```

## CLI Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Run the whole flow -- set up if needed, then scan and upload |
| `cc-sentiment setup` | Link your GitHub account for attributable uploads |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |

## Development

See `AGENTS.md` for conventions. Each component has its own:
- `server/AGENTS.md` -- Modal backend, TimescaleDB, GPG/SSH verification
- `app/AGENTS.md` -- SvelteKit, Chart.js, Vercel
- `client/AGENTS.md` -- macOS CLI, MLX inference, signing
