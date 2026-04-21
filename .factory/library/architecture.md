# Architecture — `cc-sentiment setup` flow

High-level picture of the subsystem under redesign. Implementation details live in the code.

## Subsystem boundary

The `cc-sentiment setup` TUI flow is a single Textual `ModalScreen` (`SetupScreen(Dialog[bool])`) that owns a state machine progressing through six logical steps. Its sole contract with the rest of the app is:

- **Input:** an `AppState` (from `cc_sentiment.models`).
- **Output:** `dismiss(True)` with `state.config` populated, or `dismiss(False)` (user cancelled).
- **Side effect:** `AppState.save()` persists `~/.cc-sentiment/state.json`.

Everything else — discovery, signing, credential probing — is pushed through already-split collaborators.

## Component map

```
                               ┌─────────────────────┐
                               │  SetupScreen (new)  │
                               │  state machine      │
                               └──────────┬──────────┘
                                          │
      ┌───────────────────┬───────────────┼───────────────┬───────────────────┐
      ▼                   ▼               ▼               ▼                   ▼
  StepHeader         StepBody       StepActions      KeyPreview          PendingStatus
  (title +           (body          (right-aligned   (scrollable         (spinner +
   explainer)        rhythm)         button row)      key panel)          muted text)

         ┌──────────────────────────┐      ┌─────────────────────────┐
         │  AutoSetup (status.py)   │      │  KeyDiscovery           │
         │  GitHub / openpgp probes │─────▶│  (signing/discovery.py) │
         │  StatusEmitter feedback  │      │  local SSH / GPG / gist │
         └──────────────────────────┘      └─────────────────────────┘

         ┌──────────────────────────┐      ┌─────────────────────────┐
         │  Uploader                │      │  AppState               │
         │  probe_credentials()     │      │  (models/config.py)     │
         │  → AuthOk / Unauth /     │      │  save()/load()          │
         │   Unreachable / Error    │      │  frozen Pydantic        │
         └──────────────────────────┘      └─────────────────────────┘
```

## Flow state machine

Six logical steps, driven by `ContentSwitcher`:

1. `loading` — auto-setup attempts. `StatusEmitter` streams progress lines; if one succeeds, the screen short-circuits to `done`.
2. `username` — user enters GitHub username. Optional path; "I don't use GitHub" skips to `discovery`.
3. `discovery` — local key listing (SSH + GPG). Single `RadioSet`; no `DataTable` duplicate. "Create a new cc-sentiment key" appears when no local keys + environment supports generation.
4. `remote` — per-key presence check against GitHub / keys.openpgp.org. ELIDED entirely when the selected key is already on a remote (no visible step).
5. `link` — collapse of the former RadioSet + buttons. One `RadioSet` of *how* to link; one primary "Link my key" button; manual path is a radio option, not a separate button.
6. `done` — three branches driven by a `verification_state` reactive:
   - `Verified` — success frame + `Contribute my stats` primary.
   - `Pending` — `PendingStatus` live timer + instructions `Card` + Exit/Retry row. Auto-polls every 10s.
   - `Failed` — error frame + same `Card` + Exit/Retry row. No CTA.

Transitions live in one place (on the screen class). Back/forward nav resets downstream widget state by construction.

## Persistence rule (honest-state invariant)

- `AppState.save()` is called after the user commits a key selection, BEFORE server verification. This means a crash preserves intent.
- The `Contribute my stats` CTA is bound to a `verification_ok: reactive[bool]` — it is visible IF AND ONLY IF `/verify` has returned `200` in the current session. The on-disk config alone does NOT imply the CTA.

## External dependencies (stubbed in tests)

| Surface | Prod endpoint | Test stub |
| --- | --- | --- |
| GitHub keys | `https://github.com/<u>.keys`, `.gpg` | mitmproxy addon |
| GitHub API | `https://api.github.com/users/<u>` | mitmproxy addon |
| keys.openpgp.org | `https://keys.openpgp.org/vks/v1/*` | mitmproxy addon |
| dashboard | `https://sentiments.cc/verify` | mitmproxy addon |
| `gh` CLI | local subprocess | PATH-prepended fake binary |
| `gpg` CLI | local subprocess | PATH-prepended fake binary |
| `ssh-keygen` | local subprocess | real (generates key in tmp home) |

Test-harness HTTPS trust: subprocess env gets `HTTPS_PROXY=http://127.0.0.1:<port>` + `SSL_CERT_FILE=<tmp-confdir>/mitmproxy-ca-cert.pem` + `NO_PROXY=localhost,127.0.0.1,::1`. `httpx.trust_env=True` (the default) handles the rest.

## Files (owned / extended)

- `client/cc_sentiment/tui/screens/setup.py` — rebuilt.
- `client/cc_sentiment/tui/widgets/button_row.py` — right-aligned default.
- `client/cc_sentiment/tui/widgets/step_header.py`, `step_body.py`, `step_actions.py`, `key_preview.py`, `pending_status.py` — new.
- `client/cc_sentiment/tui/screens/dialog.py` — Enter/Escape bindings inherited.
- `client/cc_sentiment/tui/status.py` — untouched if possible (AutoSetup logic is fine).
- `client/tests/test_tui.py` — extended, updated where semantics changed.
- `client/tests/tuistory/` — new harness + scenarios.

## Invariants workers must preserve

1. `SetupScreen` extends `Dialog[bool]`, dismisses with a `bool`.
2. `SSHConfig` / `GPGConfig` / `GistConfig` / `AppState` public shapes unchanged.
3. `Uploader.probe_credentials(config)` return types unchanged.
4. No regressions in non-setup TUI surfaces (processing, moments, stats, cost review, stat share, booting, platform error).
5. Minimum terminal: 80×24. No horizontal scroll, no clipped buttons.
6. Exactly one `variant="primary"` per step; primary is rightmost; Enter activates it; Escape cancels.
