# User Testing

Guidance for validators on testing surface, required tools, concurrency, and isolation.

## Two Non-Overlapping Test Layers

The mission uses **exactly two** TUI testing layers. Do not mix them:

1. **Milestones 1 & 2 — Textual `Pilot` (in-process, headless).**
   - All unit and integration tests live in `client/tests/test_tui.py`.
   - Run in the same Python process as the test; no subprocess, no tty.
   - Covers: button placement, widget geometry, focus, keyboard bindings, state transitions, copy, CSS, live-polling reactive updates, end-state branches.
   - **Never** invoke the `tuistory` skill, `tuistory` binary, or spawn `uv run cc-sentiment setup` at this layer.

2. **Milestone 3 — `tuistory` binary via shell wrapper (pty-isolated subprocess).**
   - Wrapper: `client/tests/tuistory/bin/run_scenario.sh`. Uses `script -q /dev/null` to allocate a fresh pty for the child, so the **parent tty is never inherited or captured**. Wrapper redirects `stdin=/dev/null` before `exec`.
   - Pytest is the assertion layer: fixtures invoke the wrapper via `subprocess.run(..., stdin=DEVNULL, capture_output=True)`, then read snapshots and a state.json the wrapper wrote to disk.
   - Covers: true end-to-end cross-feature flows against real keystrokes, real subprocess I/O, real HTTPS stubbing via mitmproxy.
   - **Never** invoke the `tuistory` skill via the Skill tool. **Never** run `tuistory` or `uv run cc-sentiment setup` as a foreground command in the worker's own shell. **Never** use `pty.openpty()` / `pexpect` inside Python — the wrapper is the only path.

## Validation Surface

- **Single surface:** TUI — `uv run cc-sentiment setup` launched only by M3's wrapper script (or in-process via `Pilot` for M1/M2).
- **M3 driver:** `tuistory` binary (already installed at `~/.bun/bin/tuistory`), invoked only via `client/tests/tuistory/bin/run_scenario.sh`.
- **Pty isolation (M3):** `script -q /dev/null` inside the wrapper. This is the contractual mechanism — any alternative is a mission-boundary violation.
- **HTTPS stubbing tool:** `mitmproxy` (`mitmdump` binary). Installed per-session by `.factory/init.sh` via `uv tool install mitmproxy`. MIT, Python 3.12+.
- **`gh` / `gpg` stubbing:** PATH-prepended fake binaries in the test fixture, NOT via the proxy. These subprocesses do not honor `HTTPS_PROXY` consistently and should be stubbed at the subprocess-spawn layer.
- **HOME isolation:** Each tuistory test runs the subprocess with `HOME=<tmpdir>` so `~/.cc-sentiment/`, `~/.ssh/`, and `~/.gnupg/` are per-test-clean. This is the primary filesystem isolation mechanism.

## Validation Concurrency

- **Max concurrent TUI validators: 3.**
- Machine: ~18 GB RAM, 12 CPU cores, ~6 GB baseline usage. Usable headroom ≈ 8.4 GB (70% of 12 GB free).
- Per-session cost: tuistory (~40 MB) + mitmdump (~60 MB) + `cc-sentiment setup` Python subprocess (~250 MB) + scratch HOME dir (~5 MB) ≈ ~360 MB. Three concurrent ≈ 1.1 GB — well within budget.
- Cap of 3 (not 5) is conservative to keep the user's machine responsive; Textual startup can occasionally spike on slow disks.

## M3 Test Harness Shape (for workers)

- Single entry point: `client/tests/tuistory/bin/run_scenario.sh`. Pytest fixtures invoke this via `subprocess.run(..., stdin=DEVNULL)` and then read files off disk. No Python `pty.openpty()`; no `pexpect`; no `tuistory` Skill.
- Wrapper internals (authoritative):
  ```bash
  #!/usr/bin/env bash
  set -euo pipefail
  exec </dev/null
  scenario=$1; out=$2; size=$3; port=$4; confdir=$5; fakebin=$6; home=$7; cfg=$8
  mkdir -p "$out"
  HTTPS_PROXY="http://127.0.0.1:$port" \
  HTTP_PROXY="http://127.0.0.1:$port" \
  SSL_CERT_FILE="$confdir/mitmproxy-ca-cert.pem" \
  NO_PROXY="localhost,127.0.0.1,::1" \
  HOME="$home" \
  PATH="$fakebin:$PATH" \
  CC_SENTIMENT_SCENARIO="$cfg" \
  exec script -q /dev/null tuistory --scenario "$scenario" --size "$size" --out "$out" -- uv run cc-sentiment setup
  ```
  (Exact flags depend on tuistory's CLI; the shape is: `script -q /dev/null tuistory <opts> -- uv run cc-sentiment setup`. The `script -q /dev/null` invocation is the critical pty-isolation step.)
- Runtime-input capture note: when wrappers use `script -k` to record keyboard/input logs, escape bytes are written in caret notation (`^[` for ESC, `^[[` prefixes for control sequences) rather than raw `\x1b` bytes. Validators must decode caret notation before parsing mouse/key events.
- Session-scoped `mitmdump` fixture binds to an ephemeral port with `--set confdir=<tmp>` so the CA is per-session (never touches `~/.mitmproxy/`). Mitmdump is spawned via `subprocess.Popen([...], stdin=DEVNULL, start_new_session=True)` — no pty needed (non-interactive).
- Per-test scenario registration: the mitmproxy addon reads scenario config from `CC_SENTIMENT_SCENARIO` (a file path) and supports stateful scripted responses (counter + `time.monotonic()`).
- `httpx` already honors `trust_env=True` (default on `httpx.AsyncClient()` in this codebase), so the proxy + CA are picked up without client code changes.

## Snapshot Conventions

- Golden snapshots live under `client/tests/tuistory/setup/__snapshots__/`.
- File naming: `<scenario>/<step-name>_<width>x<height>.txt` (e.g. `auto-success/done_80x24.txt`).
- Both 80×24 and 180×68 snapshots are captured where the contract requires width resilience.
- Plain-text snapshots (ANSI stripped) are the default; ANSI snapshots (`*.ansi`) are kept only where color/style is part of the assertion.

## Live Mode

- Tagged `@pytest.mark.live`.
- Opt-in via env var `CC_SENTIMENT_E2E_LIVE=1`.
- Skipped in CI; never part of auto-validation.
- Used for smoke-testing real GitHub / keys.openpgp.org / sentiments.cc once before shipping.

## Notes for Validators

- If `mitmdump` fails to start (port conflict, cert trust issue), treat as a blocker and return to orchestrator — do not try to work around with `verify=False` or skipping scenarios.
- If a scenario's HTTPS request is NOT intercepted (e.g. `httpx` escape hatch), the subprocess env is wrong — investigate before marking the assertion blocked.
- Keep the per-session `confdir` after the session so logs/CA can be inspected; delete on green.
