# cc-sentiment

A CLI that scores your Claude Code conversations on-device and contributes aggregate numbers to an open dashboard at [sentiments.cc](https://sentiments.cc).

Your conversations stay on your device. Only signed aggregate numeric scores are uploaded; the server verifies signatures using a public key you control.

## Run it

```bash
uvx cc-sentiment
```

Needs [uv](https://docs.astral.sh/uv/). On-device scoring uses MLX on Apple Silicon when available; on other platforms the CLI still runs setup, upload, and dashboard sharing. The first run sets up a verification key (GitHub or GPG), scores transcripts in `~/.claude/projects/`, and uploads the numbers.

## What gets uploaded

Scoring runs locally on Gemma 4. The client uploads only numbers and timestamps for each 5-minute bucket of a conversation.

- Sentiment score on a 1–5 scale
- Read:edit ratio, edits-without-prior-read %, write:edit ratio, tool calls per turn, subagent spawn rate
- Turn count, thinking present/chars
- Claude model and Claude Code version
- A public verification handle (GitHub username or GPG fingerprint) used only to verify signatures

Your conversation text, file contents, file paths, and tool inputs/outputs never leave your machine.

## Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Run the whole flow. Sets up if needed, then scans and uploads. |
| `cc-sentiment setup` | Set up a verification key (GitHub or GPG) so uploads can be signed |
| `cc-sentiment scan --upload` | Score new transcripts and upload |
| `cc-sentiment scan` | Score transcripts without uploading |
| `cc-sentiment upload` | Upload previously scored results |
| `cc-sentiment rescan` | Clear state and re-score everything |

## Links

Dashboard at [sentiments.cc](https://sentiments.cc). Source and issues live on [GitHub](https://github.com/yasyf/cc-sentiment).
