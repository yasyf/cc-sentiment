---
name: tui-tests-worker
description: End-to-end tuistory + mitmproxy test worker for the cc-sentiment setup flow. Builds the harness and authors scenario tests.
---

# TUI Tests Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Features under Milestone 3 (E2E test suite):
- Building the tuistory + mitmproxy harness under `client/tests/tuistory/`.
- Authoring tuistory scenarios and golden snapshots.
- PATH-prepended fake `gh` / `gpg` binaries.
- Installing mitmproxy via `uv tool install mitmproxy`.

## Required Skills

- **`tuistory`** — MUST invoke via the Skill tool when driving or iterating on the setup subprocess. Use it for: launching the TUI, sending keystrokes, capturing snapshots, iterating on golden files.

## Work Procedure

1. **Read the assertion IDs your feature fulfills.** Read each referenced assertion in `{missionDir}/validation-contract.md` in full. Also read `.factory/library/user-testing.md`, `architecture.md`, and `.factory/services.yaml`.

2. **Audit the existing harness** (if any prior M3 feature already built it) before adding to it. Don't rebuild what's already there.

3. **Harness invariants** (enforce on every feature):
   - `mitmdump` is session-scoped, binds to an ephemeral port (`-p 0`), `--set confdir=<tmpdir>`. CA path is `<confdir>/mitmproxy-ca-cert.pem`.
   - Per-test scenario registration via a small addon protocol (env var or file the addon polls). Addon supports stateful scripted responses (counter + `time.monotonic()`).
   - Subprocess env injected by launch helper:
     - `HTTPS_PROXY=http://127.0.0.1:<port>`
     - `HTTP_PROXY=http://127.0.0.1:<port>`
     - `SSL_CERT_FILE=<confdir>/mitmproxy-ca-cert.pem`
     - `NO_PROXY=localhost,127.0.0.1,::1`
     - `HOME=<per-test-tmpdir>`
     - `PATH=<fake-bin>:<orig-PATH>`
   - PATH-fake `gh` / `gpg` are shell scripts (`#!/usr/bin/env bash`) that log invocations to a file the test reads.
   - `tuistory` launches `uv run cc-sentiment setup` as the subprocess; stdin/stdout are the tuistory pty.

4. **Author tests TDD-style** — write the scenario test, capture an initial snapshot (it will mismatch), confirm red, then iterate. Each scenario test targets one or more assertion IDs from its feature's `fulfills`.

5. **Snapshot conventions** — `client/tests/tuistory/setup/__snapshots__/<scenario>/<step>_<WxH>.txt` (plain text, ANSI stripped). 80×24 snapshots always; 180×68 where width-resilience is asserted.

6. **Dry-run gate for the first M3 feature** — before authoring scenarios, confirm:
   - `uv tool list | grep mitmproxy` → present.
   - `mitmdump --version` → prints.
   - Launch `mitmdump -p 0 --set confdir=$(mktemp -d) -s /dev/null` with a 2s timeout; capture the ephemeral port.
   - Launch `uv run cc-sentiment setup` with the proxy env set; observe that at least one HTTPS request is intercepted.
   - If ANY of these fail, return to orchestrator with the blocker. Do NOT paper over with `verify=False` or skipped scenarios.

7. **Run the suite** — `cd client && uv run pytest tests/tuistory -xvs -m "not live"`. All green.

8. **Ensure no orphan processes** — After the suite finishes, `ps aux | grep -E 'mitmdump|cc-sentiment' | grep -v grep` must return empty. If not, fix the fixture teardown.

9. **Confirm no regression** — `cd client && uv run pytest -x`. All green.

## Assertion Traceability

Each scenario feature has a `fulfills` list. Every assertion ID in it MUST be verified by at least one concrete assertion in the test (e.g. `assert "Contribute my stats" in snapshot`, or `assert (state_dir / "state.json").exists()`). No "tested by the fact that the scenario runs" hand-waving.

## Example Handoff

```json
{
  "salientSummary": "Built session-scoped mitmproxy harness at client/tests/tuistory/conftest.py + scenario-registration addon. Wrote 3 scenarios covering VAL-CROSS-001/002/003 (happy-path auto-setup, auto-fail + SSH match, auto-fail + generate gist). All 3 green; mitmdump teardown confirmed no orphan procs.",
  "whatWasImplemented": "client/tests/tuistory/conftest.py (session mitmdump fixture, scenario-registry addon, launch helper with HOME/PATH/proxy env injection, PATH-fake gh/gpg scripts). client/tests/tuistory/setup/test_happy_path.py with 3 scenarios. __snapshots__/ directory with 9 golden text files (3 scenarios × 3 steps avg). .factory/init.sh updated to install mitmproxy if absent.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "uv tool install mitmproxy", "exitCode": 0, "observation": "mitmproxy 12.1.1 installed"},
      {"command": "cd client && uv run pytest tests/tuistory -xvs -m 'not live'", "exitCode": 0, "observation": "3 passed in 14.2s"},
      {"command": "ps aux | grep -E 'mitmdump|cc-sentiment' | grep -v grep", "exitCode": 1, "observation": "no output — no orphan processes"},
      {"command": "cd client && uv run pytest -x", "exitCode": 0, "observation": "full client suite green (127 passed)"}
    ],
    "interactiveChecks": [
      {"action": "Invoked tuistory skill to render scenario 1 (auto-success) and capture snapshot", "observed": "Done screen rendered with 'Contribute my stats' button at col 64/80; matches golden"}
    ]
  },
  "tests": {
    "added": [
      {"file": "client/tests/tuistory/setup/test_happy_path.py", "cases": [
        {"name": "test_auto_setup_success_lands_on_verified_done", "verifies": "VAL-CROSS-001"},
        {"name": "test_auto_fail_then_ssh_match_lands_on_verified", "verifies": "VAL-CROSS-002"},
        {"name": "test_auto_fail_then_generate_gist_key", "verifies": "VAL-CROSS-003"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Dry-run gate fails (mitmproxy install fails, proxy not intercepting, tuistory subprocess crashes). Don't work around.
- An assertion describes behavior the TUI worker hasn't implemented yet — blocked on implementation.
- A scenario requires stubbing a real subprocess that isn't a simple `gh`/`gpg` command (e.g. `ssh-keygen` — we chose to let this run for real; if a test scenario needs it stubbed, escalate).
- Golden snapshots reveal a FLAKE (non-deterministic ordering, timestamp in output, ANSI sequence variance across terminals). Pause and fix root cause; don't commit flakes.
