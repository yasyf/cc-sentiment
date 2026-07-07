# ![cc-sentiment](https://github.com/yasyf/cc-sentiment/raw/main/docs/assets/readme-banner.webp)

**Everyone swears Claude got lazier. Bring receipts.** cc-sentiment scores your Claude Code transcripts on-device and ships ten signed metrics per 5-minute slice to the shared dashboard at [sentiments.cc](https://sentiments.cc).

[![Tests](https://github.com/yasyf/cc-sentiment/actions/workflows/tests-client.yml/badge.svg)](https://github.com/yasyf/cc-sentiment/actions/workflows/tests-client.yml)
[![PyPI](https://img.shields.io/pypi/v/cc-sentiment)](https://pypi.org/project/cc-sentiment/)
[![MIT license](https://img.shields.io/badge/license-MIT-blue)](client/LICENSE)

## Get started

```bash
uvx cc-sentiment
```

<img src="https://github.com/yasyf/cc-sentiment/raw/main/docs/assets/demo.png" alt="Terminal running 'uvx cc-sentiment --help' — options plus the setup, install, run, and debug commands" width="700">

The first run links a signing key (GitHub or GPG), scores every transcript in `~/.claude/projects/`, and uploads the numbers; your slice joins the pooled charts at [sentiments.cc](https://sentiments.cc). Needs [uv](https://docs.astral.sh/uv/).

Driving with an agent? Paste this:

```text
Run `uvx cc-sentiment` and finish the setup TUI — it links a GitHub or GPG signing key, scores my Claude Code transcripts locally, and uploads the numbers.
Then run `cc-sentiment install` to schedule the daily background run.
Verify my contribution shows up on the dashboard at https://sentiments.cc.
```

---

## Use cases

### Settle whether Claude Code behavior actually shifted

Threads like [anthropics/claude-code#42796](https://github.com/anthropics/claude-code/issues/42796) describe the same drift of fewer reads before edits and lazier patches, but every report is one person's slice. Score yours and pool it:

```bash
uvx cc-sentiment
```

<img src="https://github.com/yasyf/cc-sentiment/raw/main/docs/assets/dashboard.png" alt="Terminal running 'uvx cc-sentiment' — 42 transcripts scored to an average sentiment of 3.49, numbers uploaded to sentiments.cc" width="700">

Each 5-minute slice becomes numbers such as sentiment, read-to-edit ratio, edits without a prior read, and tool calls per turn. On the pooled charts a real shift shows up as a line bending, not a vibe.

### Contribute your sessions without uploading a single prompt

Your transcripts hold prompts, file paths, and diffs you'd never post publicly. The upload never contains them:

```bash
uvx cc-sentiment setup
```

Setup walks you through linking a GitHub or GPG signing key, with honest verified / pending / failed end-states. Scoring runs on-device with MLX on Apple Silicon, and the CLI asks before touching the fallback Claude CLI engine. Each upload is the numbers plus a signature the server verifies without learning anything else about your sessions.

### Compare behavior across Claude models and CLI versions

Was it the new model, or the CLI release that shipped under you the same week? One contributor's sessions can't separate the two:

```bash
uvx cc-sentiment install
```

A daily launchd run keeps scoring new sessions, and every slice lands tagged with the Claude model and Claude Code version that produced it, so the dashboard breaks the trends down by model while the CLI version rides along in the data.

## What gets uploaded

The client records the following per 5-minute slice of each conversation.

| Metric | What it captures |
|---|---|
| Sentiment score | 1 to 5, scored locally |
| Read-to-edit ratio | Files Claude reads before editing |
| Edits without prior read % | Edits to files Claude hadn't read this session |
| Write-to-edit ratio | File rewrites vs. surgical edits |
| Tool calls per turn | Tools invoked between user messages |
| Subagent spawns | How often Claude delegates to a subagent |
| Turn count | User-to-assistant exchanges |
| Thinking present / chars | Whether and how much Claude wrote extended thinking |
| Claude model | Which model produced the assistant turns |
| `cc_version` | Claude Code CLI version |

Plus a public verification handle, your GitHub username or GPG fingerprint, used only to find a public key and verify signatures. Conversation text, file contents, file paths, prompts, tool inputs, and tool outputs never leave your machine.

## Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Interactive TUI. Sets up if needed, then scores and uploads. |
| `cc-sentiment setup` | Re-run the setup wizard to pick, generate, or re-link a signing key. |
| `cc-sentiment run` | Score new transcripts and upload. Non-interactive; safe for cron, SSH, and launchd. |
| `cc-sentiment install` | Schedule a daily background run via launchd. |
| `cc-sentiment uninstall` | Stop and remove the scheduled run. |
| `cc-sentiment debug` | Print hardware, engine, Claude CLI, server, and Sentry probes. |

The full flag list lives in `cc-sentiment --help`.

## Architecture

```
┌─────────────┐         ┌─────────────┐         ┌─────────────┐
│   client/   │  POST   │   server/   │  fetch  │    app/     │
│  local CLI  │────────▶│  Modal API  │◀────────│  SvelteKit  │
│  scoring    │ signed  │ TimescaleDB │  SSR    │  dashboard  │
└─────────────┘ upload  └─────────────┘         └─────────────┘
```

The CLI you run, the API that verifies signatures and stores slices, and the dashboard that charts the pool all live in one repo.

Watch the experiment live at [sentiments.cc](https://sentiments.cc). Licensed under [MIT](client/LICENSE).
