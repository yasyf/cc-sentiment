# client: cross-platform CLI

Cross-platform CLI tool. Discovers Claude Code conversation transcripts, scores them with the best available engine, signs results with SSH or GPG keys, and uploads aggregate metrics to the server. Local MLX scoring is Apple Silicon-only; setup, upload, and dashboard sharing run on macOS, Linux, and Windows.

## Python Style

Root `AGENTS.md` rules apply unless overridden here. The client follows a functional, fail-fast Python style. The rules below spell out the specifics.

1. **No comments or docstrings.** Code is self-documenting via names, types, and organization. Comments only for `TODO:`, non-obvious workarounds, or temporarily disabled code. Explanatory prose belongs in PR descriptions and commit messages, not in source.

2. **Functional over imperative.** Walrus `:=`, comprehensions, chained operations. Avoid intermediate variables when a pipeline reads well. `extract_score` in `cc_sentiment/text.py` is the canonical pattern: walrus-guarded regex, no temporary `match` variable lying around.

3. **No underscore prefixes on module-level helpers, classes, constants, or free functions.** Use `__all__` in package `__init__.py` for export control. Underscore prefixes are *only* allowed on class methods that are private: called only from within their own class. Positive examples: `OMLXEngine._spawn_server`, `OMLXEngine._drain`, `OMLXEngine._make_body`, `SentimentClassifier._ensure_prompt_cache`, `Pipeline._parse_buckets_with_metrics`, `Uploader._verify_credentials`, `UploadPool._worker_loop`. Negative examples (now fixed): `parser.py:_build_message`, `engines.py:_check_frustration`, `tui.py:_format_duration`.

4. **No free-floating functions outside named pure utility modules.** Methods on classes belong in classes; everything else gets a class to live on. The named utility modules are:
   - `cc_sentiment/text.py` — `format_conversation`, `extract_score`, `MAX_CONVERSATION_CHARS`
   - `cc_sentiment/nlp.py` — spaCy lazy loader (`NLP` classmethods)
   - `cc_sentiment/lexicon.py` — AFINN + domain-overrides (`Lexicon` classmethods; async `ensure_ready` + sync `polarity`)
   - `cc_sentiment/highlight.py` — snippet styling (`Highlighter` classmethods + `HighlightSpan`/`WindowedSlice` dataclasses)
   - `cc_sentiment/transcripts/parser.py` — carve-out: hosts `Backend`-implementing class plus picklable parsing helpers (`build_message`, `python_parse_chunk`, etc.) that must stay module-level for `anyio.to_process.run_sync`
   - `cc_sentiment/patches/__init__.py` — `apply_kv_cache_patch`
   - `cc_sentiment/_transcripts_rs.pyi` — `.pyi` stub; free `def` is required syntax
   New utility modules require justification. Typer-decorated commands in `cli.py` are framework convention and stay free.

5. **Match statements for type dispatch.** `match (sys.platform, platform.machine())`, `match kind:` in `EngineFactory.build`. `if/elif` only for boolean flags or non-type-discriminated branching.

6. **Minimal `try`/`except`.** Only the line that actually throws goes inside the `try`. Use `contextlib.suppress(SomeError)` for fire-and-forget. Never bare `try/except: pass`. `OMLXEngine.warm_system_prompt` is the canonical pattern: `with contextlib.suppress(httpx.HTTPError): await self.client.post(...)`.

7. **No defensive coding, backwards-compat shims, or optional modeling.** Crash on unexpected errors. `os.environ["KEY"]`, not `.get()`. No sentinel return values. No optional fields with fallback defaults; make the field required or split the model. Pre-launch, no migrations: edit `CREATE_TABLE_SQL` directly and drop the DB if needed.

8. **Make invalid states unrepresentable.** `NewType` for branded primitives (`SentimentScore`, `BucketIndex`, `SessionId`, `PromptVersion`, `ContributorId`). Frozen `pydantic.dataclasses.dataclass(frozen=True)` for immutable data. Required fields over optionals.

9. **Flat over nested.** Early returns. Three or more levels of nesting is a smell; extract a helper or invert the condition.

10. **Module organization order.** Imports, then constants, types, helpers, classes, functions, entrypoint (in that order). `from __future__ import annotations` at the top of every file.

11. **Mutable defaults forbidden.** Use `field(default_factory=...)` / `Field(default_factory=...)`.

12. **Boy Scout rule.** When you touch a file for any reason, fix nearby style violations as you go. Don't open a separate PR for them; bundle.

## Tech Stack

- **Runtime**: Python 3.12+. Cross-platform: local MLX inference is Apple Silicon only, other platforms fall back to the `claude` CLI engine.
- **ML inference**: MLX (`mlx-lm`, optional `[mlx]` extra) for local Gemma 4 on Apple Silicon GPU; cloud `omlx` subprocess on Apple Silicon by default; `claude` CLI elsewhere.
- **Model**: `unsloth/gemma-4-E2B-it-UD-MLX-4bit` (4-bit quantized, ~2.5GB)
- **CLI**: `typer` (built on Click)
- **HTTP**: `httpx` for async uploads
- **Signing**: `ssh-keygen -Y sign` via subprocess
- **Packaging**: `uv tool install` from pyproject.toml with `[project.scripts]` entry point

## Commands

```bash
uv sync                            # Install dependencies
uv run cc-sentiment scan           # Discover and score new transcripts
uv run cc-sentiment upload         # Upload pending scores to server
uv run cc-sentiment scan --upload  # Scan and upload in one step
uv run cc-sentiment setup          # Configure a verification key
uv run pytest client/              # Run tests
```

## Directory Structure

```
client/
├── pyproject.toml
└── cc_sentiment/
    ├── __init__.py
    ├── _transcripts_rs.pyi  # PyO3 stub
    ├── benchmark.py         # BenchmarkRunner (perf + scaling tests)
    ├── cli.py               # Typer commands — thin
    ├── daemon.py            # background daemon entry
    ├── hardware.py          # platform / RAM detection
    ├── headless.py          # headless scan flow (no TUI)
    ├── nlp.py               # spaCy NLP utility (lazy-loaded)
    ├── pipeline.py          # Pipeline orchestrator
    ├── repo.py              # SQLite Repository
    ├── sentiment.py         # SentimentClassifier (MLX-only)
    ├── text.py              # format_conversation, extract_score, MAX_CONVERSATION_CHARS
    ├── upload.py            # Uploader, UploadPool, UploadProgress
    ├── models/              # split: transcript, bucket, record, stats, config
    ├── engines/             # split: protocol, filter, omlx, claude_cli, factory
    ├── signing/             # split: backends, discovery, signer
    ├── highlight.py         # Highlighter — snippet styling (AFINN + profanity + negation)
    ├── tui/                 # split: stages, progress, status, format, widgets,
    │                        #        moments_view, view, app, screens/
    ├── transcripts/         # parser, backend, rust
    └── patches/             # mlx-lm KV cache patch
```

## Optional Rust Parser

`cc_sentiment._transcripts_rs` is a PyO3 extension under `crates/transcripts/` that provides a ~5× faster implementation of `stream_parse` (full parse, rayon + crossbeam-bounded channel) and `scan_bucket_keys` (metadata-only bucket count). It's **optional**, distributed as prebuilt abi3 wheels per `(os, arch)`, with a Python implementation as a permanent fallback.

- Backends live in the `cc_sentiment.transcripts/` package: `parser.py` holds the `TranscriptParser` classmethod façade plus `TranscriptDiscovery` / `ConversationBucketer` and module constants. `backend.py` defines the `Backend` Protocol. `python.py` and `rust.py` each export a backend class implementing that Protocol. `__init__.py` selects one at import time and binds it to `TranscriptParser.BACKEND`.
- Selection is: respect `CC_SENTIMENT_DISABLE_RUST=1` to force `PythonBackend()`; else try to import `RustBackend` and fall back to `PythonBackend()` on `ImportError`. Call `TranscriptParser.backend_name()` to see which one is live.
- `setuptools-rust` with `optional = true` means an sdist install on a platform without `cargo` produces a pure-Python install. No Rust toolchain required.
- abi3 tagging is driven by a single canonical source: `[bdist_wheel] py_limited_api = cp313` in `setup.cfg`. setuptools-rust reads `bdist_wheel.py_limited_api` (see its `build.py:444-450`) to derive both the wheel tag (`cp313-abi3`) and the `pyo3/abi3-py313` Cargo feature passed to the build. `RustExtension.py_limited_api` is deprecated upstream. Leave it at its default (`"auto"`), don't duplicate the pin in `[[tool.setuptools-rust.ext-modules]]`. `pyproject` `[tool.distutils.bdist_wheel]` is not a stable config surface (see pypa/wheel#582), so `setup.cfg` is the only stable repo-level knob. `crates/transcripts/Cargo.toml` keeps `features = ["abi3-py313"]` on `pyo3` so direct `cargo build` / editable installs that skip setuptools-rust still compile against the stable ABI.

**Contributors who only touch Python code need nothing extra.** `uv sync && uv run pytest` works and exercises the Python path. The native extension is built lazily only when `cargo` is on `$PATH`.

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

## Signing

### Key Discovery

Look for local verification keys in order:
1. `~/.ssh/id_ed25519` (preferred)
2. `~/.ssh/id_rsa`
3. Local GPG keys

GitHub usernames are used only for public-key lookup. GPG fingerprints are used for OpenPGP verification.

### Setup Flow

`cc-sentiment setup`:
1. Detect GitHub CLI auth, saved username, local SSH/GPG keys, and public key locations
2. Skip setup if an already-published local key verifies with sentiments.cc
3. Recommend the safest available route: managed gist, OpenPGP email verification, guided gist, or tool install/sign-in
4. Save config only after sentiments.cc verifies a test signature

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

## Client-Specific Rules

All rules from root `AGENTS.md` and the **Python Style** section above apply, plus:

- **Submodule layout.** Each large concept (`engines`, `signing`, `models`, `tui`, `transcripts`) is a package under `cc_sentiment/`. The package `__init__.py` re-exports the public API (`from .factory import EngineFactory; ... __all__ = [...]`) so external callers `from cc_sentiment.engines import EngineFactory` keep working. One concept per file inside the package. Cross-references between split files are inter-package imports.
- **MLX isolated in `sentiment.py`.** No MLX imports leak into other modules. Keeps the CLI testable on non-Apple-Silicon (mock `cc_sentiment.sentiment`).
- **Subprocess calls use explicit argument lists.** Never `shell=True`. Always `subprocess.run(["ssh-keygen", "-Y", "sign", ...])`.
- **Local state is split between JSON and SQLite.** `~/.cc-sentiment/state.json` holds only the signing config. Derived state (records, sessions, scored buckets, file mtimes) lives in `~/.cc-sentiment/records.db` via stdlib `sqlite3` wrapped in `anyio.to_thread.run_sync`.
- **CLI commands are thin.** Parse args, call library modules, format output. No business logic in CLI handlers.
- **All network calls through `upload.py`.** Single module owns the HTTP client, base URL, retry logic. Upload **concurrency** (worker count, timeout, retry policy, backoff) is defined as module-level constants in `upload.py` (`UPLOAD_WORKER_COUNT`, `UPLOAD_POOL_TIMEOUT_SECONDS`, `WORKER_BATCH_RETRIES`, `WORKER_BACKOFF_BASE_SECONDS`). The `tui/` package must not hardcode its own values.
- **MLX is a platform-gated main dep.** `mlx-lm` lives in `[project.dependencies]` with a `sys_platform == 'darwin' and platform_machine == 'arm64'` marker, so it installs by default on Apple Silicon and is absent elsewhere. `cc_sentiment.sentiment` is only imported from the `mlx` branch in `EngineFactory.build` (`engines/factory.py`); the platform guard at the top of `sentiment.py` fails fast if the module is somehow loaded on the wrong arch.
- **Parallelism in pipeline/engine code uses anyio, not `ThreadPoolExecutor` or Textual workers.** For parallel CPU/IO work inside async library code (e.g. parsing N transcripts), the pattern is `async with anyio.create_task_group() as tg: tg.start_soon(run_one, ...)` where each task body calls `await anyio.to_thread.run_sync(sync_fn, ...)`. AnyIO's default thread limiter bounds concurrency; tune via `to_thread.current_default_thread_limiter().total_tokens` if needed. Textual `@work` decorators are reserved for UI-coupled tasks inside the `tui/` package. Never import them in `pipeline.py`, `engines/`, or any module also consumed by `headless.py`. `ThreadPoolExecutor` is only used in `benchmark.py` (sync CLI entry with `click.progressbar`); new async code should not reach for it.
- **Training and eval live outside the repo.** Dataset build, LoRA training, DSPy optimization, ship scripts (e.g. `compress_adapter.py`), and full-eval gates are in `~/Code/cc-sentiments-local/` (symlinked here as `./experiments`, gitignored). Training deps (mlx-lm[train], dspy-ai, anthropic, datasets, scikit-learn, scipy) never ship in the wheel.

### TUI state and view conventions (`tui/`)

- **TUI state lives in dataclasses, not loose attributes.** Scoring state lives on `ScoringProgress` (`tui/progress.py`); upload state lives on `UploadProgress` (`upload.py`). A new long-running phase gets its own dataclass with a `reset()` method. No floating `self._foo_count` / `self._bar_in_flight` counters on `CCSentimentApp`. Reactive properties (`scored`, `total`) are thin mirrors of dataclass fields and exist only to trigger Textual watchers; read directly from the dataclass (`self._upload.uploaded_records`) when no watcher is needed. The dataclass is the source of truth.
- **No ad-hoc `query_one()` in App methods.** All widget reads/writes on the processing screen go through `ProcessingView` (`tui/view.py`). If you need a new widget update, add a method to `ProcessingView` instead of reaching into `query_one()` from `tui/app.py`. This keeps the widget tree and its mutations in one place and makes reset/teardown a single call.
- **Reset is one call, not a checklist.** Adding a phase means adding `<phase>.reset()` on its dataclass and (if it owns widgets) a helper on `ProcessingView`. `_reset_for_rescan` and flow init must call the same reset helpers. Never duplicate field-by-field resets between the two paths.
- **Upload orchestration lives in `UploadPool`, not the App.** Worker pool, memory streams, retry/backoff, and timeout enforcement belong in `upload.py`. The App supplies a `producer` coroutine and an `on_progress_change` callback and nothing else. Don't grow new `_upload_*` methods on `CCSentimentApp`.
- **Progress bars show progress; status text shows context.** Any long-running phase that produces discrete units of work gets a `ProgressBar`. Status text explains what's happening and where (e.g. "Uploading to sentiments.cc"), not how much is done. Don't regress to text-only "X/Y batches" indicators.
- **Widget updates driven by reactive watchers.** Instead of calling `self._render_*()` imperatively from 8 places, assign to a reactive attribute (or reassign the dataclass in place and nudge a reactive) and put the update logic in `watch_*`. Imperative paint calls are a smell.
- **One screen per file under `tui/screens/`.** `BootingScreen`, `PlatformErrorScreen`, `StatShareScreen`, `SetupScreen`. The screen class is the only top-level export. Its private helpers stay underscore-prefixed methods on the screen class itself.
