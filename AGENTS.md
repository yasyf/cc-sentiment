# cc-sentiment Development Guide

Monorepo with three components: `server/` (Modal API), `app/` (Svelte dashboard), and `client/` (macOS CLI).

## Repository Structure

```
cc-sentiment/
├── server/           # Modal backend — upload API, data query API, timeseries storage
├── app/              # Svelte frontend — dashboard, charts, caching
├── client/           # macOS CLI — transcript parsing, MLX inference, signed upload
├── AGENTS.md         # This file — shared conventions
└── README.md         # Project overview
```

## Python Style (server/ and client/)

Target Python 3.14 for server, 3.12+ for client. Both use `uv` for dependency management.

**No comments or docstrings.** Code is self-documenting via names, types, and organization. Comments only for TODOs, non-obvious workarounds, or disabled code.

**Functional over imperative.** Chain operations, use walrus `:=`, comprehensions over loops. No intermediate variables when a pipeline reads clearly.

**No underscore prefixes.** Use `__all__` for export control, not naming conventions.

**No free-floating functions.** Methods on classes, except pure utility modules.

**Match statements for type dispatch.** `if/elif` only for meaningful boolean flags.

**Minimal try/except.** Only the throwing line inside `try`. No broad exception handlers.

**No defensive coding, backwards-compat, or optional modeling.** No fallbacks or shims. Crash on unexpected errors — `os.environ["KEY"]` not `.get()`. No sentinel values. No optional fields with fallback defaults — make fields required or split the model.

**Make invalid states unrepresentable.** `NewType` for branded primitives. Frozen dataclasses for immutable data. Required fields over optionals.

**Flat over nested.** Early returns, flat control flow. Nesting >3 levels is a smell.

**Type annotations everywhere.** `from __future__ import annotations`. All function signatures, all variables where the type isn't obvious.

**Fail fast and loud.** Assertions for invariants. No silent degradation.

## General Rules

**Minimal changes.** Stay within scope. Fix the issue, then stop.

**Match surrounding code.** Priority: (1) component AGENTS.md, (2) this file, (3) same file, (4) same module.

**Mechanical linting.** Do not run `ruff`, `black`, `isort`, `prettier`, `eslint` manually. These are handled by pre-commit hooks or CI. Only fix issues requiring human judgment.

**Testing.** Tests live next to the code they test. Strict assertions. Mock external dependencies, not the code under test.

**Git.** Commits should be atomic and scoped. One logical change per commit.
