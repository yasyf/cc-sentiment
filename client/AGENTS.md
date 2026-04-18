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

## Optional Rust Parser

`cc_sentiment._transcripts_rs` is a PyO3 extension under `crates/transcripts/` that provides a ~5× faster implementation of `parse_line` / `parse_file` / `bucket_keys_for` / `scan_bucket_keys`. It's **optional** — distributed as prebuilt abi3 wheels per `(os, arch)`, with a Python implementation as a permanent fallback.

- Backends live in the `cc_sentiment.transcripts/` package: `parser.py` holds the `TranscriptParser` classmethod façade plus `TranscriptDiscovery` / `ConversationBucketer` and module constants. `backend.py` defines the `Backend` Protocol. `python.py` and `rust.py` each export a backend class implementing that Protocol. `__init__.py` selects one at import time and binds it to `TranscriptParser.BACKEND`.
- Selection is: respect `CC_SENTIMENT_DISABLE_RUST=1` → `PythonBackend()`; else try to import `RustBackend` and fall back to `PythonBackend()` on `ImportError`. Call `TranscriptParser.backend_name()` to see which one is live.
- `setuptools-rust` with `optional = true` means an sdist install on a platform without `cargo` produces a pure-Python install — no Rust toolchain required.
- abi3 tagging depends on three things agreeing on the same floor (cp313): Cargo feature `abi3-py313` on the `pyo3` dep, `py-limited-api = "cp313"` in `[[tool.setuptools-rust.ext-modules]]` in `pyproject.toml`, and `[bdist_wheel] py_limited_api = cp313` in `setup.cfg`. The Cargo feature picks which stable-ABI symbol set to compile against; the setuptools-rust pin wires the pyo3 feature; `setup.cfg` is the thing that actually makes `bdist_wheel` stamp `cp313-abi3` onto the wheel filename. Dropping any one of the three produces a non-abi3 wheel.

**Contributors who only touch Python code need nothing extra** — `uv sync && uv run pytest` works and exercises the Python path. The native extension is built lazily only when `cargo` is on `$PATH`.

**Contributors working on the Rust crate** need rustup:

```bash
brew install rustup-init && rustup-init -y
uv pip install -e . --force-reinstall   # rebuilds the extension
cd crates/transcripts && cargo test      # unit tests for the Rust side
```

Parity tests in `tests/test_transcripts.py` are parametrized over both backends via a `backend` fixture that monkeypatches `TranscriptParser.BACKEND` with `PythonBackend()` / `RustBackend()` (skipping the rust param when the extension isn't importable). Any change to the Python parser must be mirrored in the Rust crate, and vice versa.

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
- **All network calls through `upload.py`.** Single module owns the HTTP client, base URL, retry logic. Upload **concurrency** (worker count, timeout, retry policy, backoff) is defined as module-level constants in `upload.py` (`UPLOAD_WORKER_COUNT`, `UPLOAD_POOL_TIMEOUT_SECONDS`, `WORKER_BATCH_RETRIES`, `WORKER_BACKOFF_BASE_SECONDS`). `tui.py` must not hardcode its own values.
- **MLX is an optional `[mlx]` extra.** `cc_sentiment.sentiment` is only imported from the `mlx` engine branch in `engines.py`, after a `find_spec("mlx_lm")` check raises an install hint when the extra is missing. The platform guard at the top of `sentiment.py` fails fast if the module is somehow loaded on the wrong arch.
- **Parallelism in pipeline/engine code uses anyio, not `ThreadPoolExecutor` or Textual workers.** For parallel CPU/IO work inside async library code (e.g. parsing N transcripts), the pattern is `async with anyio.create_task_group() as tg: tg.start_soon(run_one, ...)` where each task body calls `await anyio.to_thread.run_sync(sync_fn, ...)`. AnyIO's default thread limiter bounds concurrency; tune via `to_thread.current_default_thread_limiter().total_tokens` if needed. Textual `@work` decorators are reserved for UI-coupled tasks in `tui.py` — never import them in `pipeline.py`, `engines.py`, or any module also consumed by `headless.py`. `ThreadPoolExecutor` is only used in `benchmark.py` (sync CLI entry with `click.progressbar`); new async code should not reach for it.

### TUI state and view conventions (`tui.py`)

- **TUI state lives in dataclasses, not loose attributes.** Scoring state → `ScoringProgress`; upload state → `UploadProgress`. A new long-running phase gets its own dataclass with a `reset()` method. No floating `self._foo_count` / `self._bar_in_flight` counters on `CCSentimentApp`. Reactive properties (`scored`, `total`, `uploaded_count`) are thin mirrors of dataclass fields and exist only to trigger Textual watchers — the dataclass is the source of truth.
- **No ad-hoc `query_one()` in App methods.** All widget reads/writes on the processing screen go through `ProcessingView`. If you need a new widget update, add a method to `ProcessingView` rather than reaching into `query_one()` from the App. This keeps the widget tree and its mutations in one place and makes reset/teardown a single call.
- **Reset is one call, not a checklist.** Adding a phase means adding `<phase>.reset()` on its dataclass and (if it owns widgets) a helper on `ProcessingView`. `_reset_for_rescan` and flow init must call the same reset helpers — never duplicate field-by-field resets between the two paths.
- **Upload orchestration lives in `UploadPool`, not the App.** Worker pool, memory streams, retry/backoff, and timeout enforcement belong in `upload.py`. The App supplies a `producer` coroutine and an `on_progress_change` callback and nothing else. Don't grow new `_upload_*` methods on `CCSentimentApp`.
- **Progress bars show progress; status text shows context.** Any long-running phase that produces discrete units of work gets a `ProgressBar`. Status text explains what's happening and where (e.g. "Uploading to sentiments.cc"), not how much is done. Don't regress to text-only "X/Y batches" indicators.
- **Widget updates driven by reactive watchers.** Instead of calling `self._render_*()` imperatively from 8 places, assign to a reactive attribute (or reassign the dataclass in place and nudge a reactive) and put the update logic in `watch_*`. Imperative paint calls are a smell.
