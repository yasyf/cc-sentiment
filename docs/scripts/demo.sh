#!/usr/bin/env bash
# Regenerates docs/assets/demo.png from a real run of `uvx cc-sentiment --help`.
# Requires freeze (https://github.com/charmbracelet/freeze). Rich colorizes the
# help itself under FORCE_COLOR; the run reads no transcripts and uploads nothing.
set -euo pipefail

cd "$(dirname "$0")/../.."

tmpdir="$(mktemp -d -t cc-sentiment-demo)"
trap 'rm -rf "$tmpdir"' EXIT
out="$tmpdir/demo.ansi"

printf '$ uvx cc-sentiment --help\n' >"$out"
env -u UV_EXCLUDE_NEWER FORCE_COLOR=1 CC_SENTIMENT_NO_TELEMETRY=1 uvx cc-sentiment --help >>"$out"

freeze "$out" \
  --language ansi \
  --theme github-dark \
  --background "#0d1117" \
  --window \
  --padding 24 \
  --font.family "Menlo" \
  --font.size 28 \
  --output docs/assets/demo.png
