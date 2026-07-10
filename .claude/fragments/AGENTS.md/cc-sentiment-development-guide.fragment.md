# cc-sentiment Development Guide

Monorepo with three components: `server/` (Modal API), `app/` (Svelte dashboard), and `client/` (cross-platform CLI).

## Repository Structure

```
cc-sentiment/
├── server/           # Modal backend — upload API, data query API, timeseries storage
├── app/              # Svelte frontend — dashboard, charts, caching
├── client/           # Cross-platform CLI — transcript parsing, scoring, signed upload
├── AGENTS.md         # This file — shared conventions
└── README.md         # Project overview
```

Training and eval live in a sibling repo at `~/Code/cc-sentiments-local/` (note the `-local` suffix and the plural). Dataset build, LoRA training, DSPy/SIMBA prompt optimization, swarm dispatcher (`harness/run_swarm.py`), leaderboard, and pre-ship regression gates are there. The shipped adapter at `client/cc_sentiment/adapter/adapters.safetensors.zst` is the output of that repo's ship recipe — see its `README.md`.
