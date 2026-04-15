# cc-sentiment

[![PyPI](https://img.shields.io/pypi/v/cc-sentiment.svg)](https://pypi.org/project/cc-sentiment/)
[![Python](https://img.shields.io/badge/python-3.13%2B-blue.svg)](https://www.python.org/downloads/)
[![macOS](https://img.shields.io/badge/macOS-Apple%20Silicon-black.svg)](https://www.apple.com/mac/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](client/LICENSE)

An open experiment in Claude Code sentiment — does it vary by time of day, day of week, or model?

Contributors run a local CLI that scores their Claude Code transcripts on-device with Gemma 4 via MLX, then uploads only the numeric scores (not the conversations) to a shared dashboard at [sentiments.cc](https://sentiments.cc).

![Dashboard](docs/dashboard.png)

## Why this exists

Threads like [anthropics/claude-code#42796](https://github.com/anthropics/claude-code/issues/42796) catalogued sharp shifts in Claude Code behavior — read:edit ratios collapsing, edits landing without prior reads, more lazy patches than research. Those shifts were felt across the community but hard to measure outside one person's terminal.

This is a community experiment to measure those signals continuously, on real conversations, and put the numbers somewhere everyone can see.

## Quick start

Two paths, same CLI.

**Fast path (recommended)** — keeps the [omlx](https://github.com/jundot/omlx) grammar-constrained inference engine:

```bash
uvx --from https://sentiments.cc/run cc-sentiment
```

**PyPI** — discoverable but skips the omlx fast engine in favor of `mlx-lm` fallback (PyPI rejects direct git URLs in dependencies):

```bash
uvx cc-sentiment
```

Requires macOS on Apple Silicon, Python 3.13+, and [uv](https://docs.astral.sh/uv/).

The bare command walks you through setup (linking your GitHub account so uploads are attributable), scores your transcripts, and uploads the scores.

## What we measure

Per 5-minute bucket of each conversation:

| Metric | What it captures |
|---|---|
| Sentiment score | 1–5 Likert, scored locally by Gemma 4 |
| Read:edit ratio | How many files Claude reads before making an edit |
| Edits without prior read % | Share of edits where Claude hadn't yet read the file in this session |
| Write:edit ratio | Share of file writes vs. surgical edits |
| Tool calls per turn | How many tools Claude invokes between user messages |
| Subagent spawns | How often Claude delegates to a subagent |
| Turn count | Number of user → assistant exchanges |
| Thinking present / chars | Whether and how much Claude wrote extended thinking |
| Claude model | Which model produced the assistant turns |
| `cc_version` | Claude Code CLI version on the user's machine |

Plus the contributor's GitHub handle, so uploads are attributable.

## What stays on your machine

Conversation text, file contents, file paths, tool inputs, and tool outputs **never leave your Mac**. Only numbers, timestamps, and the metadata above are uploaded. Scoring runs locally on Apple Silicon — no inference traffic leaves the device.

## Architecture

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   client/   │  POST   │   server/   │  fetch  │    app/     │
│  macOS CLI  │────────▶│  Modal API  │◀────────│  SvelteKit  │
│  MLX+Gemma4 │ signed  │ TimescaleDB │  SSR    │  dashboard  │
└─────────────┘ upload  └─────────────┘         └─────────────┘
```

## CLI commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Run the whole flow — set up if needed, then scan and upload |
| `cc-sentiment setup` | Link your GitHub account for attributable uploads |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |

## Development

See `AGENTS.md` for conventions. Each component has its own:
- `server/AGENTS.md` — Modal backend, TimescaleDB, GPG/SSH verification
- `app/AGENTS.md` — SvelteKit, Chart.js, Vercel
- `client/AGENTS.md` — macOS CLI, MLX inference, signing

## License

[MIT](client/LICENSE)
