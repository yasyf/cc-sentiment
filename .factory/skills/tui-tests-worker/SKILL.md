---
name: tui-tests-worker
description: End-to-end tuistory + mitmproxy test worker for the cc-sentiment setup flow. Builds the shell-wrapper harness and authors scenario tests.
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

**None.** Do **NOT** invoke the `tuistory` skill via the Skill tool, and do **NOT** spawn Task subagents that invoke it. The `tuistory` skill drives the *foreground* terminal — when invoked inside a worker session (or any subagent), it captures the parent tty and hijacks keyboard input into the orchestrator's session. This is a hard prohibition.

## Architecture Choice (authoritative)

There are two TUI testing layers in this mission, and they do NOT overlap:

- **Milestones 1 & 2** use Textual's in-process `Pilot` (`pytest-textual`). No subprocesses. No tty. Covers buttons, focus, keys, state transitions, copy, widget geometry.
- **Milestone 3 (this skill)** uses the **`tuistory` binary** (installed at `~/.bun/bin/tuistory`) invoked **only via a committed shell-wrapper script**. The wrapper uses `script -q /dev/null` to allocate a fresh pty for the child, so the parent tty is never inherited or captured. Pytest is the assertion layer — it invokes the wrapper as a `subprocess.run(..., stdin=DEVNULL, stdout=PIPE)` fixture and then reads the snapshots/state files the wrapper wrote to disk.

The pattern is:
```
pytest fixture  ──calls──>  client/tests/tuistory/bin/run_scenario.sh  ──internally uses──>  script -q /dev/null -F <log> tuistory <args> -- uv run cc-sentiment setup
                                                                                              (child has a FRESH pty; parent tty untouched)
```

NEVER: invoke the `tuistory` Skill, run `uv run cc-sentiment setup` directly from the worker's shell, run `tuistory` as a foreground command in the worker's own shell, or use `pty.openpty()`/`pexpect` inside Python. The shell wrapper is the ONLY path.

## Work Procedure

1. **Read the assertion IDs your feature fulfills.** Read each referenced assertion in `{missionDir}/validation-contract.md` in full. Also read `.factory/library/user-testing.md`, `architecture.md`, and `.factory/services.yaml`.

2. **Audit the existing harness** (if any prior M3 feature already built it) before adding to it. Don't rebuild what's already there.

3. **Harness invariants** (enforce on every feature):
   - `client/tests/tuistory/bin/run_scenario.sh` is the single entry point. It accepts: scenario name, output dir, size (WxH), proxy port, confdir, fake-bin dir, home dir, scenario-config path.
   - Inside the wrapper: `exec script -q /dev/null tuistory <args> -- env HTTPS_PROXY=... HTTP_PROXY=... SSL_CERT_FILE=... NO_PROXY=... HOME=... PATH=<fake-bin>:<orig> uv run cc-sentiment setup`. `stdin=/dev/null` MUST be redirected before `exec`. `script -q /dev/null` allocates a fresh pty for `tuistory` so no parent-tty inheritance.
   - `mitmdump` runs under a session-scoped pytest fixture that also invokes it via `subprocess.Popen([...], stdin=DEVNULL, stdout=PIPE, stderr=PIPE, start_new_session=True)` — no pty needed for mitmdump (it's non-interactive). Binds to an ephemeral port (`-p 0`), `--set confdir=<tmpdir>`. CA path is `<confdir>/mitmproxy-ca-cert.pem`.
   - Per-test scenario registration via a small addon protocol (env var or file the addon polls). Addon supports stateful scripted responses (counter + `time.monotonic()`).
   - Per-test env passed to the wrapper (not inherited from parent):
     - `HTTPS_PROXY=http://127.0.0.1:<port>`
     - `HTTP_PROXY=http://127.0.0.1:<port>`
     - `SSL_CERT_FILE=<confdir>/mitmproxy-ca-cert.pem`
     - `NO_PROXY=localhost,127.0.0.1,::1`
     - `HOME=<per-test-tmpdir>`
     - `PATH=<fake-bin>:<orig-PATH>`
   - PATH-fake `gh` / `gpg` are shell scripts (`#!/usr/bin/env bash`) under `client/tests/tuistory/_fixtures/fake-bin/` that log invocations to a file the test reads.
   - The wrapper writes snapshots to `<output-dir>/<step>_<WxH>.txt` and an exit-state JSON to `<output-dir>/state.json`. Pytest reads those files; it does NOT interact with the subprocess.

4. **Author tests TDD-style** — write the scenario test, capture an initial snapshot (it will mismatch), confirm red, then iterate. Each scenario test targets one or more assertion IDs from its feature's `fulfills`. Tests look like:
   ```python
   def test_auto_setup_success_lands_on_verified_done(tuistory_session, scenario_registry):
       scenario_registry.load("auto-success")
       out = tmp_path / "out"
       result = subprocess.run(
           [WRAPPER, "auto-success", str(out), "80x24", str(tuistory_session.port), str(tuistory_session.confdir), str(FAKE_BIN), str(home), str(scenario_registry.path)],
           stdin=subprocess.DEVNULL,
           capture_output=True,
           timeout=30,
       )
       assert result.returncode == 0, result.stderr.decode()
       snapshot = (out / "done_80x24.txt").read_text()
       assert "Contribute my stats" in snapshot
   ```

5. **Snapshot conventions** — `client/tests/tuistory/setup/__snapshots__/<scenario>/<step>_<WxH>.txt` (plain text, ANSI stripped). 80×24 snapshots always; 180×68 where width-resilience is asserted.

   **Deterministic snapshots only.** Transient states (especially `loading_*`, mid-progress, mid-network-probe) MUST NOT be snapshotted unless the scenario config explicitly holds the state in place until tuistory confirms capture (e.g. by delaying an HTTP response via the mitmproxy addon). If you cannot guarantee a stable capture point, do NOT snapshot that stage — rely on post-transition stable snapshots (discovery / remote / done / failed). Committing a flaky snapshot blocks every subsequent scenario.

   **Stage-history assertions go through state.json, not snapshots.** If a scenario needs to assert "stage X was/wasn't visited", persist `SetupScreen.transition_history` to the wrapper's exit `state.json` and assert on that. Sampled snapshots can miss transient renders.

   **Keyboard-only assertions need event-log evidence.** Asserting "no mouse clicks were used" by inspecting only the scripted tuistory command list is insufficient — it doesn't prove the runtime didn't synthesize a click. Ensure the wrapper/driver writes the tuistory event log and assert there are zero `click` / `mouse-down` / `mouse-up` events.

6. **Dry-run gate for the first M3 feature** — before authoring scenarios, confirm (ALL via the wrapper script; NEVER in the worker's own foreground shell):
   - `uv tool list | grep mitmproxy` → present.
   - `mitmdump --version` → prints (run as `subprocess.run([...], stdin=DEVNULL)` — non-interactive).
   - Start `mitmdump -p 0 --set confdir=$(mktemp -d) -s /dev/null` as a background `subprocess.Popen` with `stdin=DEVNULL`; capture the ephemeral port from stdout.
   - Invoke the wrapper once with a trivial scenario (e.g. just "launch and press q"); confirm exit code 0, confirm `<output-dir>/done_80x24.txt` was written, confirm at least one HTTPS request was logged by mitmdump.
   - If ANY of these fail, return to orchestrator with the blocker. Do NOT paper over with `verify=False`, do NOT skip scenarios, do NOT fall back to `pty.openpty()` or the `tuistory` Skill.

7. **Run the suite** — `cd client && uv run pytest tests/tuistory -xvs -m "not live"`. All green.

8. **Ensure no orphan processes** — After the suite finishes, the harness-owned process check must be empty. The check has TWO components:
   (a) Process-name match: `ps aux | grep -E 'mitmdump|tuistory|cc-sentiment setup|uv run cc-sentiment setup|tests.tuistory.fake_bin' | grep -v grep` → must be empty.
   (b) Per-test HOME descendant match: any process whose `HOME` env points into a per-test tmpdir (e.g. `/tmp/pytest-of-*/tuistory-*` or `/private/tmp/...`) is a harness child that escaped teardown. Parse via `ps -Eo pid,command` (or `lsof` on the tmpdir) and assert zero matches.
   Note: the broader `cc-sentiment` grep without the narrower terms also matches unrelated pre-existing user processes (other sessions' pytest runs, `vite preview` for app/, pre-existing `keyboxd` under unrelated HOMEs) — those are not harness orphans.
   If either check finds harness-owned procs, fix the fixture teardown (send SIGTERM, then SIGKILL after 2s). The wrapper `run_scenario.sh` should also kill any temp-HOME descendants on exit.

9. **Driver forced-close semantics** — `driver.py` must default to treating a forced-close (subprocess didn't exit cleanly within timeout) as a FAILURE. Scenarios that intentionally exercise forced-close (e.g. "Escape during loading") must explicitly opt in via `allow_forced_close=True` (or equivalent flag). Never quietly treat timeout as success.

10. **Harness teardown must survive startup failures** — Fixture-level cleanup (mitmdump.terminate, tempdir cleanup, process reaping) must be registered BEFORE any health-check that could raise. Use `try/finally`, `ExitStack.callback(...)`, or `pytest.fixture` with yield inside try/finally. A raised health check must never leak mitmdump.

9. **Confirm no regression** — `cd client && uv run pytest -x`. All green.

## Assertion Traceability

Each scenario feature has a `fulfills` list. Every assertion ID in it MUST be verified by at least one concrete assertion reading a file on disk (snapshot text, state.json contents, fake-bin invocation log). No "tested by the fact that the scenario runs" hand-waving.

## Example Handoff

```json
{
  "salientSummary": "Built wrapper-script harness at client/tests/tuistory/bin/run_scenario.sh (uses `script -q /dev/null` for pty isolation) + session mitmdump fixture + scenario-registry addon. Wrote 3 scenarios covering VAL-CROSS-001/002/003. All 3 green; parent tty never touched; no orphan procs.",
  "whatWasImplemented": "client/tests/tuistory/bin/run_scenario.sh (shell wrapper, pty-isolated via script -q /dev/null, stdin=/dev/null redirect, writes snapshots + state.json). client/tests/tuistory/conftest.py (session mitmdump fixture, scenario-registry addon, fake-bin path). client/tests/tuistory/_fixtures/fake-bin/{gh,gpg} (bash stubs). client/tests/tuistory/setup/test_happy_path.py with 3 scenarios that invoke the wrapper via subprocess.run and read snapshots. __snapshots__/ directory with 9 golden text files.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "uv tool install mitmproxy", "exitCode": 0, "observation": "mitmproxy 12.1.1 installed"},
      {"command": "cd client && uv run pytest tests/tuistory -xvs -m 'not live'", "exitCode": 0, "observation": "3 passed in 14.2s"},
      {"command": "ps aux | grep -E 'mitmdump|tuistory|cc-sentiment' | grep -v grep", "exitCode": 1, "observation": "no orphan processes"},
      {"command": "cd client && uv run pytest -x", "exitCode": 0, "observation": "full client suite green (127 passed)"}
    ],
    "interactiveChecks": []
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

- Dry-run gate fails (mitmproxy install fails, proxy not intercepting, wrapper crashes, snapshots empty). Don't work around.
- `script -q /dev/null` behaves differently than expected on this machine — escalate; don't reach for `pty.openpty()`/`pexpect`/the Skill.
- An assertion describes behavior the TUI worker hasn't implemented yet — blocked on implementation.
- A scenario requires stubbing a real subprocess that isn't a simple `gh`/`gpg` command (e.g. `ssh-keygen` — we chose to let this run for real; if a test scenario needs it stubbed, escalate).
- Golden snapshots reveal a FLAKE (non-deterministic ordering, timestamp in output, ANSI sequence variance across terminals). Pause and fix root cause; don't commit flakes.
