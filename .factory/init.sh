#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT/client"

if [ ! -d ".venv" ] || [ ! -f ".venv/bin/python" ]; then
  uv sync
fi

uv sync --quiet

if ! uv tool list 2>/dev/null | grep -q '^mitmproxy '; then
  uv tool install mitmproxy
fi

if ! command -v tuistory >/dev/null 2>&1; then
  echo "warning: tuistory not on PATH; expected at ~/.bun/bin/tuistory"
fi

mkdir -p tests/tuistory/setup tests/tuistory/_fixtures
