# LoRA campaigns — training & shipping the sentiment classifier

How the shipped `client/cc_sentiment/adapter/adapters.safetensors.zst` gets built, evaluated, and replaced. Read this before editing `SYSTEM_PROMPT` in `client/cc_sentiment/engines/protocol.py` or the adapter itself.

## Why this doc exists

Commit `25573ed` (Apr 21) edited the prompt — added a session-resume ambiguity guard — without retraining the LoRA (which was fit against the pre-edit prompt in `99127d0`) and without running any eval. The previous eval set lived under `experiments/` in a worktree that was subsequently torn down, so nothing measured the change. Campaign `d` rebuilds the harness as a persistent workspace outside this repo so that can't happen again, and ships a checked-in regression gate so silent drift is impossible.

## The harness lives outside this repo

```
~/Code/cc-sentiments-local/   <──  symlinked as ./experiments (gitignored)
├── pyproject.toml            # separate uv project — mlx-lm[train], dspy, anthropic, scipy, etc.
├── harness/
│   ├── build_dataset.py      # TranscriptHarvest + SessionResumeEnumerator + SyntheticGenerator
│   ├── data_format.py        # ChatFormat (mlx-lm "messages" jsonl)
│   ├── eval.py               # ProductionEval — mirrors shipped inference
│   ├── run_lora.py           # mlx_lm.lora.run wrapper + RunResult sidecar
│   ├── run_full_ft.py        # fine_tune_type=full variant
│   ├── run_dspy.py           # OMLX-backed DSPy optimization
│   ├── run_swarm.py          # manifest + --check control plane
│   ├── leaderboard.py        # WinnerPicker — mean exact_acc, McNemar, session-resume floor
│   └── regression_test.py    # developer-only full-eval gate
├── configs/                  # per-experiment JSON (d0_baseline, d3_lora_long, …)
├── dataset/
│   ├── raw.jsonl
│   ├── train.jsonl, val.jsonl
│   └── frozen/eval_v2.jsonl + eval_v2.sha256
├── adapters/<id>__seed<N>/   # mlx-lm training outputs
└── results/<id>__seed<N>.json + manifest.json + winner.json + report.md
```

Training deps never ship in the `cc-sentiment` wheel. Harness code stays out of the main repo history. If you want to version-control your own runs, `git init` inside `~/Code/cc-sentiments-local/` — that's yours, not the project's.

The local dir is automatically symlinked into each worktree as `./experiments` (gitignored). The symlink is a convenience; everything lives behind it.

## Reproduce a campaign

```bash
cd ~/Code/cc-sentiments-local
uv sync
export ANTHROPIC_API_KEY=...   # required for synthetic label generation

# 1. Build the dataset (real-transcript harvest + SessionResumeEnumerator + Opus-labeled synthetic)
uv run python -m harness.build_dataset --full
uv run python -m harness.build_dataset --stats   # prints per-class counts + session-resume matrix
uv run python -m harness.build_dataset --verify  # sha256 check on frozen + smoke

# 2. Build the manifest (17 cells: d0…d8 and the d4 rank×scale sweep)
uv run python -m harness.run_swarm --manifest

# 3. Run experiments. Each cell × seed writes results/<id>__seed<N>.json.
#    Fan out via the Claude Code Agent swarm (see "Swarm dispatch" below) or serially:
uv run python -m harness.run_lora    --experiment-id d3_lora_long --seed 1729 --config configs/d3_lora_long.json
uv run python -m harness.run_full_ft --experiment-id d5_full_ft   --seed 1729 --config configs/d5_full_ft.json
uv run python -m harness.run_dspy    --experiment-id d7_dspy_mipro --seed 1729 --config configs/d7_dspy_mipro.json
# … etc for every cell × every seed

uv run python -m harness.run_swarm --check  # verifies every expected result file is present + matches manifest

# 4. Pick a winner
uv run python -m harness.leaderboard --report --winner
cat results/report.md

# 5. Full-eval gate (developer-only) before shipping
uv run python -m harness.regression_test
```

## Swarm dispatch (parallel experiments)

`run_swarm.py` is the control plane: it writes the manifest and verifies results, but it does *not* spawn compute. Parallel execution happens via a Claude Code `Agent` swarm — one sub-agent per `(experiment_id, seed)` cell, all dispatched in one `Agent` message so they run concurrently. Each sub-agent:

- Pins cc-sentiment HEAD (reads `git -C /Users/yasyf/Code/cc-sentiment rev-parse HEAD`).
- Pins dataset digest (reads `dataset/frozen/eval_v2.sha256`).
- Runs its assigned `python -m harness.run_lora …` or `.run_dspy …` invocation.
- Writes exactly one `results/<id>__seed<N>.json` and nothing else.
- Never commits, never edits outside `adapters/<id>__seed<N>/` and `results/`.

Controller trusts `run_swarm --check`, not the sub-agent's summary text. GPU pinning per sub-agent is by `MLX_METAL_DEVICE=<k>` (Apple) or `CUDA_VISIBLE_DEVICES=<k>` (if the user's machine has CUDA). On a single-GPU host, degrade N to 1.

Batching (driven by shared-resource constraints, not arbitrary chunking):

- **B0** — `d0_baseline`, `d1_prompt_noLoRA` (eval-only, 2 parallel)
- **B1** — `d2_lora_short`, `d3_lora_long`, and the `d4_lora_rank_r{R}_s{S}` sweep × 3 seeds each (up to N GPUs)
- **B2** — `d5_full_ft` × 3 seeds (memory-heavy, serial on most hosts)
- **B3** — `d6_dspy_bootstrap`, `d7_dspy_mipro`, `d8_dspy_copro` × 3 seeds (3 parallel, distinct OMLX ports)

## Winner bar

`WinnerPicker` enforces all of:

- Mean `exact_acc` across 3 seeds ≥ `d0_baseline + 0.02`.
- No per-class F1 regression > 0.05 on any seed.
- `tag_accuracy["session_resume"]` ≥ 0.85 on the aggregate (the failure mode that motivated this campaign must actually be fixed).
- McNemar test vs baseline: p < 0.05 on ≥ 2 of 3 seeds.

If nothing clears the bar, ship `d1_prompt_noLoRA` as a fallback and queue `d10_prompt_a8` (hand-tuned prompt variants) as a follow-up.

## Ship flow

```bash
# in ~/Code/cc-sentiments-local/
ID=$(python -c "import orjson,pathlib; print(orjson.loads(pathlib.Path('results/winner.json').read_bytes())['experiment_id'])")
SEED=1729

cp "adapters/${ID}__seed${SEED}/adapters.safetensors" \
   /Users/yasyf/Code/cc-sentiment/client/cc_sentiment/adapter/adapters.safetensors

cd /Users/yasyf/Code/cc-sentiment
uv run python client/scripts/compress_adapter.py
rm client/cc_sentiment/adapter/adapters.safetensors
```

Then write `client/cc_sentiment/adapter/metadata.json`:

```json
{
  "adapter_dtype": "BF16",
  "dataset_digest": "<from results/<id>__seed<seed>.json>",
  "cc_sentiment_git_sha": "<same>",
  "experiment_id": "<winner id>",
  "mean_exact_acc": 0.96,
  "std_exact_acc": 0.008,
  "seeds": [1729, 42, 2024],
  "mcnemar_p_vs_baseline": 0.003,
  "session_resume_tag_acc": 0.91,
  "adapter_sha256": "<sha256 of adapters.safetensors.zst>",
  "smoke_eval_sha256": "<sha256 of client/tests/fixtures/frozen_eval_v2_smoke.jsonl>",
  "mlx_lm_version": "0.31.2",
  "train_command": "python -m harness.run_lora --experiment-id <id> --seed 1729"
}
```

Update `client/cc_sentiment/adapter/adapter_config.json` only if rank/scale/num_layers changed from what ships.

Finally commit:

```
feat(client): ship dN_winner bf16 LoRA
```

## Regression gates

### Always-on (part of `pytest client/`)

- `test_adapter.py::TestAdapterCodecRoundtrip::test_byte_exact[f32|bf16]` — round-trip on committed safetensors fixtures. Locks the F32/BF16 plane-shuffle cipher.
- `test_adapter.py::TestAdapterCodecRoundtrip::test_rejects_mixed_dtype` — encode fails loudly on heterogeneous adapters.
- `test_adapter.py::TestAdapterCodecRoundtrip::test_dtype_roundtrips_report_correctly` — `AdapterCodec.dtype()` reports what the shipped zst actually contains.

### MLX-opt-in (`pytest client/ -m slow`)

- `test_adapter.py::test_shipped_digest_matches_metadata` — `AdapterCodec.digest()` matches `metadata.json[adapter_sha256]`; smoke fixture sha matches `metadata.json[smoke_eval_sha256]`. Catches "edited the zst or the smoke fixture but forgot to bump metadata."
- `test_adapter.py::test_smoke_eval_accuracy_floor` — loads shipped adapter, runs the 30-sample `fixtures/frozen_eval_v2_smoke.jsonl`, asserts `exact_acc ≥ 0.90`.
- `test_adapter.py::test_smoke_eval_session_resume_floor` — same loader, filters to `tag == "session_resume"`, asserts `exact_acc == 1.0`. This is the specific regression gate for the failure that motivated campaign d.

### Developer-only (full frozen eval)

`uv run python -m harness.regression_test` — runs the full 150+ sample frozen eval against the shipped adapter, asserts `exact_acc ≥ 0.94` and `session_resume ≥ 0.85`. Run before every adapter ship.

## Prompt versioning

`client/cc_sentiment/models/bucket.py:PROMPT_VERSION` stays `"v1"` pre-launch. Post-launch policy: bump on any prompt mutation, ship the bumped version in the same commit as a retrained adapter, never edit the prompt without a matching adapter retrain.

## The session-resume failure mode

The `25573ed` prompt rule enumerates five canonical phrases: `continue`, `continue from where you left off`, `pick up where you left off`, `resume`, `keep going`. `harness/label.py:SessionResumeEnumerator` produces a deterministic matrix over `(phrase × context × paired_sentiment × surface_form)` so every canonical phrase appears in every cell — bare neutral (→3), after praise (→4), after complaint (→1), with resume markers (`[context restored]`, `/compact just ran`), and in ALL-CAPS / Title Case / lowercase. `build_dataset --stats` fails if any canonical phrase is missing from the matrix. The smoke-eval fixture is stratified to include at least one session-resume sample per paired sentiment, so `test_smoke_eval_session_resume_floor` regresses if the failure reopens.
