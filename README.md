# cc-sentiment

Quantifying Claude Code frustration over time.

Motivated by [claude-code#42796](https://github.com/anthropics/claude-code/issues/42796) -- when Claude Code regresses, developers get frustrated. This project measures that frustration signal directly, independent of any internal telemetry, and surfaces it as a public dashboard.

**Dashboard**: [app-anetaco.vercel.app](https://app-anetaco.vercel.app)

## Quick Start

```bash
pip install cc-sentiment

# One-time setup: detects your GitHub SSH/GPG keys
cc-sentiment setup

# Scan transcripts and upload sentiment scores
cc-sentiment scan --upload
```

Requires macOS with Apple Silicon (for local Gemma 4 inference via MLX).

## How It Works

1. The CLI discovers Claude Code transcripts in `~/.claude/projects/`
2. Each conversation is split into time buckets and scored 1-5 locally using Gemma 4 on MLX
3. Scores are signed with your GitHub SSH or GPG key
4. The server verifies signatures against your public keys on GitHub and stores the data
5. The dashboard shows sentiment trends, usage patterns, and whether peak hours correlate with worse scores

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
| `cc-sentiment setup` | Configure GitHub username and signing key |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |
| `cc-sentiment benchmark` | Benchmark inference engines |

## Development

See `AGENTS.md` for conventions. Each component has its own:
- `server/AGENTS.md` -- Modal backend, TimescaleDB, GPG/SSH verification
- `app/AGENTS.md` -- SvelteKit, Chart.js, Vercel
- `client/AGENTS.md` -- macOS CLI, MLX inference, signing
