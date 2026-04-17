# client/ — macOS CLI

macOS Apple Silicon CLI tool. Discovers Claude Code conversation transcripts, runs them through Gemma 4 via MLX for local sentiment analysis, signs results with GitHub SSH keys, and uploads to the server.

## Tech Stack

- **Runtime**: Python 3.12+. Cross-platform — local MLX inference is Apple Silicon only, other platforms fall back to the `claude` CLI engine.
- **ML inference**: MLX (`mlx-lm`, optional `[mlx]` extra) for local Gemma 4 on Apple Silicon GPU; cloud `omlx` subprocess on Apple Silicon by default; `claude` CLI elsewhere.
- **Model**: `unsloth/gemma-4-E2B-it-UD-MLX-4bit` (4-bit quantized, ~2.5GB)
- **CLI**: `click` or `typer`
- **HTTP**: `httpx` for async uploads
- **Signing**: `ssh-keygen -Y sign` via subprocess
- **Packaging**: `uv tool install` from pyproject.toml with `[project.scripts]` entry point

## Commands

```bash
uv sync                            # Install dependencies
uv run cc-sentiment scan           # Discover and score new transcripts
uv run cc-sentiment upload         # Upload pending scores to server
uv run cc-sentiment scan --upload  # Scan and upload in one step
uv run cc-sentiment setup          # Configure GitHub username, verify SSH keys
uv run pytest client/              # Run tests
```

## Directory Structure (planned)

```
client/
├── pyproject.toml
├── cli.py              # CLI entry point (click/typer commands)
├── transcripts.py      # Transcript discovery and parsing
├── sentiment.py        # MLX inference, prompt construction, score extraction
├── signing.py          # GitHub SSH key discovery and payload signing
├── upload.py           # HTTP client for server API
├── models.py           # Pydantic models for transcripts, scores, payloads
└── tests/
    ├── test_transcripts.py
    ├── test_sentiment.py
    ├── test_signing.py
    └── fixtures/
        └── sample_transcript.jsonl
```

## Transcript Discovery

Claude Code stores conversations at `~/.claude/projects/<project-slug>/<uuid>.jsonl`. Each line is a JSON object representing a conversation turn.

The client:
1. Walks `~/.claude/projects/` recursively for `.jsonl` files
2. Tracks already-processed files in `~/.cc-sentiment/state.json`
3. Skips files unchanged since last scan (by mtime)
4. Parses each JSONL file into a conversation

### JSONL Structure

Each line contains (at minimum):
- `type`: message type (`human`, `assistant`, `tool_use`, `tool_result`, etc.)
- `message`: the content object
- Timestamps in conversation metadata

Extract user messages (`type: "human"`) as the primary sentiment signal. Error tool results and assistant apologies are secondary signals.

## MLX Inference

### Model Loading

```python
from mlx_lm import load, generate

model, tokenizer = load("unsloth/gemma-4-E2B-it-UD-MLX-4bit")
```

Model is downloaded once and cached in `~/.cache/huggingface/`.

### Sentiment Prompt

Score each conversation on a 1-5 Likert scale:
- **1** — Deeply frustrated, angry, giving up
- **2** — Annoyed, things aren't working
- **3** — Neutral, transactional
- **4** — Satisfied, things are working
- **5** — Delighted, impressed, flow state

The prompt must:
- Present the conversation (or representative sample if too long)
- Ask for a single integer score with brief justification
- Use structured output (JSON) for reliable extraction
- Be versioned — prompt changes shift the dataset, so the version is included in uploaded records

### Inference Config

- `max_tokens`: 100 (score + short justification)
- `temperature`: 0.0 (deterministic scoring)
- Each conversation gets its own inference call (context matters)

## GitHub SSH Signing

### Key Discovery

Look for SSH keys in order:
1. `~/.ssh/id_ed25519` (preferred)
2. `~/.ssh/id_rsa`
3. Keys listed in `~/.ssh/config`

GitHub username from `git config github.user` or `~/.cc-sentiment/config.toml`.

### Setup Flow

`cc-sentiment setup`:
1. Ask for GitHub username (or read from git config)
2. Fetch `https://github.com/<username>.keys`
3. Find matching local private key
4. If no match: print instructions for adding an SSH key to GitHub
5. Save config to `~/.cc-sentiment/config.toml`

### Signing Protocol

```bash
echo '<canonical_json>' | ssh-keygen -Y sign -f ~/.ssh/id_ed25519 -n cc-sentiment
```

Namespace `cc-sentiment` prevents signature reuse across applications. Canonical JSON = sorted keys, compact encoding, no whitespace.

## Upload Protocol

1. Collect pending records (scored but not yet uploaded) from `~/.cc-sentiment/state.json`
2. Serialize records to canonical JSON
3. Sign with user's SSH key
4. `POST` to server `/upload` with signed payload
5. On success, mark records as uploaded in state
6. On failure, retain for retry on next run

## Style Specifics

All rules from root `AGENTS.md` apply, plus:

- **MLX isolated in `sentiment.py`.** No MLX imports leak into other modules. Keeps the CLI testable on non-Apple-Silicon (mock sentiment module).
- **Subprocess calls use explicit argument lists.** Never `shell=True`. Always `subprocess.run(["ssh-keygen", "-Y", "sign", ...])`.
- **Local state is split between JSON and SQLite.** `~/.cc-sentiment/state.json` holds only the signing config. Derived state (records, sessions, scored buckets, file mtimes) lives in `~/.cc-sentiment/records.db` via stdlib `sqlite3` wrapped in `anyio.to_thread.run_sync`.
- **CLI commands are thin.** Parse args, call library modules, format output. No business logic in CLI handlers.
- **All network calls through `upload.py`.** Single module owns the HTTP client, base URL, retry logic.
- **MLX is an optional `[mlx]` extra.** `cc_sentiment.sentiment` is only imported from the `mlx` engine branch in `engines.py`, after a `find_spec("mlx_lm")` check raises an install hint when the extra is missing. The platform guard at the top of `sentiment.py` fails fast if the module is somehow loaded on the wrong arch.
