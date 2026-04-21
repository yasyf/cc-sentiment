# User Testing

Guidance for validators on testing surface, required tools, concurrency, and isolation.

## Validation Surface

- **Single surface:** TUI — `uv run cc-sentiment setup` launched as a subprocess.
- **Testing tool:** `tuistory` (already installed at `~/.bun/bin/tuistory`).
- **HTTPS stubbing tool:** `mitmproxy` (`mitmdump` binary). Installed per-session by `.factory/init.sh` via `uv tool install mitmproxy`. MIT, Python 3.12+.
- **`gh` / `gpg` stubbing:** PATH-prepended fake binaries in the test fixture, NOT via the proxy. These subprocesses do not honor `HTTPS_PROXY` consistently and should be stubbed at the subprocess-spawn layer.
- **HOME isolation:** Each tuistory test runs the subprocess with `HOME=<tmpdir>` so `~/.cc-sentiment/` and `~/.ssh/` and `~/.gnupg/` are per-test-clean. This is the primary isolation mechanism.

## Validation Concurrency

- **Max concurrent TUI validators: 3.**
- Machine: ~18 GB RAM, 12 CPU cores, ~6 GB baseline usage. Usable headroom ≈ 8.4 GB (70% of 12 GB free).
- Per-session cost: tuistory (~40 MB) + mitmdump (~60 MB) + `cc-sentiment setup` Python subprocess (~250 MB) + scratch HOME dir (~5 MB) ≈ ~360 MB. Three concurrent ≈ 1.1 GB — well within budget.
- Cap of 3 (not 5) is conservative to keep the user's machine responsive; Textual startup can occasionally spike on slow disks.

## Test Harness Shape (for workers)

- Session-scoped `mitmdump` fixture binds to an ephemeral port with `--set confdir=<tmp>` so the CA is per-session (never touches `~/.mitmproxy/`).
- Per-test scenario registration: the addon reads scenario config from a file or env var updated between tests.
- Subprocess env injected by the launch helper:
  - `HTTPS_PROXY=http://127.0.0.1:<port>`
  - `HTTP_PROXY=http://127.0.0.1:<port>`
  - `SSL_CERT_FILE=<confdir>/mitmproxy-ca-cert.pem`
  - `NO_PROXY=localhost,127.0.0.1,::1`
  - `HOME=<per-test-tmpdir>`
  - `PATH=<fake-bin-dir>:<original-PATH>`
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
