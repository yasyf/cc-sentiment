---
name: tui-worker
description: Textual/TUI feature worker for cc-sentiment setup flow redesign. Implements flow logic, widgets, and CSS; writes pytest tests.
---

# TUI Worker

NOTE: Startup and cleanup are handled by `worker-base`. This skill defines the WORK PROCEDURE.

## When to Use This Skill

Any feature that touches:
- `client/cc_sentiment/tui/screens/setup.py`
- `client/cc_sentiment/tui/screens/dialog.py`
- `client/cc_sentiment/tui/widgets/button_row.py`
- New widgets under `client/cc_sentiment/tui/widgets/` (step_header, step_body, step_actions, key_preview, pending_status)
- `client/cc_sentiment/tui/status.py` (narrow changes only)
- `client/tests/test_tui.py` (unit/integration tests via Textual's `Pilot`)

## Required Skills

None. Do **NOT** invoke the `tuistory` skill. Do **NOT** spawn any Task subagent that invokes the `tuistory` skill, runs `uv run cc-sentiment setup`, `cc-sentiment`, `uv run cc-sentiment …` interactively, `script`, `expect`, or any other process that attaches to a terminal. These grab the parent tty and hijack the orchestrator's keyboard input. All behavioral verification in M1/M2 is done through Textual's in-process `Pilot` (inside `pytest`), which runs headless and NEVER touches any tty.

## Work Procedure

1. **Read context** — The feature description + its `fulfills` assertion IDs. Read each referenced assertion in `{missionDir}/validation-contract.md` in full before coding. Also read `/Users/yasyf/Code/cc-sentiment/.factory/library/architecture.md`, `ui-primitives.md`, and `/Users/yasyf/Code/cc-sentiment/client/AGENTS.md`.

2. **Audit the current state** — Before editing, skim the relevant file(s) end-to-end. Understand the state machine around your feature. Do NOT assume the previous feature's scope is identical to what's in the code now.

3. **Write tests first (TDD)** — Extend `client/tests/test_tui.py` with failing tests that map to the assertion IDs your feature fulfills. Use the `SetupHarness` pattern. Tests MUST fail first (red). Example structure:
   ```python
   async def test_primary_button_is_rightmost_on_username_step(no_auto_setup):
       async with SetupHarness(AppState()).run_test() as pilot:
           await pilot.pause(delay=0.3)
           screen = pilot.app.screen
           actions = screen.query_one(StepActions)
           buttons = list(actions.query(Button))
           primary = next(b for b in buttons if b.variant == "primary")
           assert buttons.index(primary) == len(buttons) - 1
   ```
   Run `cd client && uv run pytest tests/test_tui.py -xvs -k <new-test>` and confirm the red.

4. **Implement** — Match `client/AGENTS.md` style strictly:
   - No comments / docstrings.
   - Functional style; `match` for type dispatch; frozen dataclasses for state.
   - No ad-hoc `query_one` in App methods — new widgets get their own class with methods.
   - `from __future__ import annotations` at top.
   - No defensive coding; crash on unexpected errors; `os.environ["KEY"]`, not `.get()`.
   - Method-level underscore prefix is fine; module-level names are clean.

5. **Run the full test suite** — `cd client && uv run pytest tests/test_tui.py -xvs`. All tests green.

6. **Render verification via Pilot only** — If you need to inspect rendered geometry/text, use `Pilot` in a pytest case: `async with SetupHarness(AppState()).run_test(size=(80, 24)) as pilot: ...` then read `pilot.app.screen` / `widget.region` / `pilot.app.screen.render_line(...)`. **DO NOT** run `uv run cc-sentiment setup`, `cc-sentiment`, `script`, `expect`, `tuistory`, or any subprocess that attaches to a terminal. **DO NOT** spawn Task subagents that invoke the `tuistory` skill or run the setup subprocess. Any such command is forbidden because it captures the orchestrator's tty and hijacks keyboard input into the wrong session. E2E subprocess-level verification belongs exclusively to Milestone 3 (`tui-tests-worker`), inside a pytest fixture with an isolated pty.

7. **Confirm no regression elsewhere** — `cd client && uv run pytest -x` (full client test suite). All green.

8. **Diff review** — `git diff client/cc_sentiment/tui/` before commit. Confirm no stray changes outside mission boundaries.

## Assertion Traceability

Every feature has a `fulfills` list. For each assertion ID, your implementation MUST produce observable behavior that matches the assertion's description. Keep a running note in your handoff: "VAL-X-001 satisfied by: <widget/behavior>". Do NOT mark a feature complete if any of its `fulfills` assertions are not verifiably satisfied by your code.

## Example Handoff

```json
{
  "salientSummary": "Introduced StepActions widget; reworked SetupScreen to compose every step's button row via StepActions with exactly one variant='primary' right-aligned. All VAL-NAV-001/002/019 assertions verified via new Pilot tests and manual 80x24 render check.",
  "whatWasImplemented": "Added client/cc_sentiment/tui/widgets/step_actions.py (new Horizontal subclass with align-horizontal: right + leading Spacer). Updated SetupScreen.compose_* methods (6 steps) to use StepActions instead of ButtonRow. Added 7 new pytest cases in test_tui.py covering primary-rightmost, one-primary-per-step, tab-order-primary-last.",
  "whatWasLeftUndone": "",
  "verification": {
    "commandsRun": [
      {"command": "cd client && uv run pytest tests/test_tui.py -xvs", "exitCode": 0, "observation": "54 passed, 0 failed"},
      {"command": "cd client && uv run pytest -x", "exitCode": 0, "observation": "full client suite green"}
    ],
    "interactiveChecks": [
      {"action": "Rendered username step at 80x24 via Pilot (in-process, headless), captured widget geometry", "observed": "Primary Next button x-offset=54, secondary 'I don't use GitHub' x-offset=30; Spacer absorbs slack"}
    ]
  },
  "tests": {
    "added": [
      {"file": "client/tests/test_tui.py", "cases": [
        {"name": "test_step_actions_primary_is_rightmost", "verifies": "VAL-NAV-002"},
        {"name": "test_exactly_one_primary_per_step", "verifies": "VAL-NAV-001"},
        {"name": "test_tab_order_primary_last", "verifies": "VAL-NAV-019"}
      ]}
    ]
  },
  "discoveredIssues": []
}
```

## When to Return to Orchestrator

- Worker cannot satisfy an assertion without touching files outside mission boundaries (engines, pipeline, upload.py except narrow additions).
- An assertion contradicts another assertion or contradicts the code's observable behavior in a way the worker cannot resolve.
- Existing tests outside the setup flow begin failing unexpectedly — mission boundary violation somewhere; return for diagnosis.
- `Uploader.probe_credentials` or `AppState.save` API needs a breaking change — must be justified.
