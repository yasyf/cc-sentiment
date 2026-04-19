# cc-sentiment

A macOS CLI that scores your Claude Code conversations on-device and contributes the numbers to an open dashboard at [sentiments.cc](https://sentiments.cc).

Your conversations stay on your Mac. Only anonymous numeric scores are uploaded.

## Run it

```bash
uvx cc-sentiment
```

Needs macOS on Apple Silicon and [uv](https://docs.astral.sh/uv/). The first run links your GitHub account, scores transcripts in `~/.claude/projects/`, and uploads the numbers.

## What gets uploaded

Scoring runs locally on Gemma 4. The client uploads only numbers and timestamps for each 5-minute bucket of a conversation.

- Sentiment score on a 1–5 scale
- Read:edit ratio, edits-without-prior-read %, write:edit ratio, tool calls per turn, subagent spawn rate
- Turn count, thinking present/chars
- Claude model and Claude Code version
- Your GitHub handle, so uploads can be attributed

Your conversation text, file contents, file paths, and tool inputs/outputs never leave your machine.

## Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Run the whole flow. Sets up if needed, then scans and uploads. |
| `cc-sentiment setup` | Link your GitHub account for attributable uploads |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |

## Links

Dashboard at [sentiments.cc](https://sentiments.cc). Source and issues live on [GitHub](https://github.com/yasyf/cc-sentiment).
