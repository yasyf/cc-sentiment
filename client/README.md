# cc-sentiment

A macOS CLI that scores your Claude Code conversations on-device and contributes the numbers to an open dashboard at [sentiments.cc](https://sentiments.cc).

Your conversations stay on your Mac. Only anonymous numeric scores are uploaded.

## Install & run

The fast path — keeps the [omlx](https://github.com/jundot/omlx) grammar-constrained inference engine:

```bash
uvx --from https://sentiments.cc/run cc-sentiment
```

Or from PyPI (falls back to the pure `mlx-lm` engine):

```bash
uvx cc-sentiment
```

Requires macOS on Apple Silicon, Python 3.13+, and [uv](https://docs.astral.sh/uv/).

The bare command walks you through setup (linking your GitHub account so uploads are attributable), scores your transcripts, and uploads the scores.

## What gets uploaded

Only numbers and timestamps. For each 5-minute bucket of a conversation:

- Sentiment score (1–5, scored locally by Gemma 4)
- Read:edit ratio, edits-without-prior-read %, write:edit ratio, tool calls per turn, subagent spawn rate
- Turn count, thinking present/chars
- Claude model and Claude Code version
- Your GitHub handle (so uploads are attributable)

Your conversation text, file contents, file paths, and tool inputs/outputs never leave your machine.

## Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Run the whole flow — set up if needed, then scan and upload |
| `cc-sentiment setup` | Link your GitHub account for attributable uploads |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |

## Links

- Dashboard: [sentiments.cc](https://sentiments.cc)
- Source: [github.com/yasyf/cc-sentiment](https://github.com/yasyf/cc-sentiment)
- Issues: [github.com/yasyf/cc-sentiment/issues](https://github.com/yasyf/cc-sentiment/issues)
