# Environment

Environment variables, external dependencies, and setup notes for workers.

**What belongs here:** required env vars, external API keys/services, dependency quirks, platform-specific notes.
**What does NOT belong here:** service ports/commands (see `.factory/services.yaml`).

---

## Platform

- **Target:** macOS Apple Silicon for the client; the setup flow code is cross-platform (pure Python + stdlib subprocess + httpx).
- **Python:** 3.13 for the client (`client/pyproject.toml` pins `requires-python = ">=3.13"`).
- **uv:** used for dependency management. Run `uv sync` inside `client/` to create `.venv`.

## Required tools

- `uv` — Python package manager. Installed on the dev machine.
- `tuistory` — TUI testing. Installed at `~/.bun/bin/tuistory`.
- `mitmproxy` / `mitmdump` — HTTPS stubbing for tuistory tests. Installed via `uv tool install mitmproxy` in `.factory/init.sh`.

## Runtime env vars (consumed by the app)

- None required for the setup flow itself.
- `CC_SENTIMENT_DISABLE_RUST=1` — forces the Python transcript parser (unrelated to setup; noted for completeness).

## Test-only env vars

- `CC_SENTIMENT_E2E_LIVE=1` — opt-in for live-mode tuistory tests (hits real github.com / keys.openpgp.org / sentiments.cc). Skipped in CI.
- `HTTPS_PROXY`, `HTTP_PROXY`, `SSL_CERT_FILE`, `NO_PROXY` — injected per-test subprocess by the tuistory harness to route HTTPS through `mitmdump`.
- `HOME` — overridden per-test to `<tmpdir>` so `~/.cc-sentiment/`, `~/.ssh/`, `~/.gnupg/` are per-test-clean.
- `PATH` — prepended with a fake-bin dir that contains `gh` / `gpg` shell-script stubs.

## External services

- `https://github.com/<u>.keys`, `.gpg` — public GitHub key endpoints. No auth needed. Rate limits apply in live mode.
- `https://api.github.com/users/<u>` — existence check for a username. No auth needed.
- `https://keys.openpgp.org/vks/v1/{by-fingerprint,upload,request-verify}` — GPG public key registry.
- `https://sentiments.cc/verify` — cc-sentiment dashboard credential probe (signed request → 200/401/5xx).

All four are stubbed via the mitmproxy addon in validator runs; no credentials required in stubbed mode.

## Config files

- `~/.cc-sentiment/state.json` — app state (contains `config: ClientConfig`). Written by `AppState.save()`.
- `~/.cc-sentiment/keys/id_ed25519` — cc-sentiment-managed SSH key (for gist path).
- `~/.mitmproxy/` — NEVER written by tests. Tests use `--set confdir=<tmp>` so the CA lives in a session-scoped tmp dir.

## Coding conventions (reference)

- See `/Users/yasyf/Code/cc-sentiment/client/AGENTS.md` for full Python style rules (no comments, functional, match dispatch, frozen dataclasses, fail-fast).
- TUI state conventions: state lives on dataclasses; no ad-hoc `query_one` from App methods; reset is one call.
