# cc-sentiment

A CLI that scores your Claude Code conversations and contributes aggregate numbers to an open dashboard at [sentiments.cc](https://sentiments.cc).

Your conversations stay on your device. Only signed aggregate numeric scores are uploaded to sentiments.cc; the server verifies signatures using a public key you control.

## Run it

```bash
uvx cc-sentiment
```

Needs [uv](https://docs.astral.sh/uv/). Local scoring uses MLX on Apple Silicon when available; on other platforms the CLI still runs setup, upload, and dashboard sharing, and asks before using the configured fallback engine. The first run sets up a verification key (GitHub or GPG), scores transcripts in `~/.claude/projects/`, and uploads the numbers.

## What gets uploaded

The client uploads only numbers and timestamps for each 5-minute bucket of a conversation.

- Sentiment score on a 1 to 5 scale
- Read-to-edit ratio, edits-without-prior-read %, write-to-edit ratio, tool calls per turn, subagent spawn rate, turn count, and thinking present/chars
- Claude model and Claude Code version
- A public verification handle, your GitHub username or GPG fingerprint, used only to find a public key and verify signatures

Your conversation text, file contents, file paths, prompts, tool inputs, and tool outputs are not uploaded to sentiments.cc.

## Commands

| Command | Description |
|---------|-------------|
| `cc-sentiment` | Interactive TUI. Sets up if needed, then scores and uploads. |
| `cc-sentiment setup` | Re-run the setup wizard to pick, generate, or re-link a signing key. |
| `cc-sentiment run` | Score new transcripts and upload. Non-interactive; safe for cron, SSH, and launchd. |
| `cc-sentiment install` | Schedule a daily background run via launchd. |
| `cc-sentiment uninstall` | Stop and remove the scheduled run. |
| `cc-sentiment debug` | Print hardware, engine, Claude CLI, server, and Sentry probes. |

## Links

Dashboard at [sentiments.cc](https://sentiments.cc). Source and issues live on [GitHub](https://github.com/yasyf/cc-sentiment).
