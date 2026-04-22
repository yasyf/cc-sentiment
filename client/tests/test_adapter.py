from __future__ import annotations

import hashlib
import json
from importlib.util import find_spec
from pathlib import Path

import orjson
import pytest

from cc_sentiment.adapter import AdapterCodec

FIXTURES: Path = Path(__file__).parent / "fixtures"
F32_SAMPLE: Path = FIXTURES / "adapter_f32_sample.safetensors"
BF16_SAMPLE: Path = FIXTURES / "adapter_bf16_sample.safetensors"
SMOKE_EVAL: Path = FIXTURES / "frozen_eval_v2_smoke.jsonl"
METADATA: Path = AdapterCodec.DIR / "metadata.json"

MLX_AVAILABLE: bool = find_spec("mlx_lm") is not None


class TestAdapterCodecRoundtrip:
    @pytest.mark.parametrize(
        "fixture",
        [F32_SAMPLE, BF16_SAMPLE],
        ids=["f32", "bf16"],
    )
    def test_byte_exact(self, fixture: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(AdapterCodec, "ZST", tmp_path / "adapters.safetensors.zst")
        AdapterCodec.encode(fixture)
        decoded = tmp_path / "decoded.safetensors"
        AdapterCodec.decode(decoded)
        assert (
            hashlib.sha256(fixture.read_bytes()).hexdigest()
            == hashlib.sha256(decoded.read_bytes()).hexdigest()
        )

    def test_dtype_roundtrips_report_correctly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(AdapterCodec, "ZST", tmp_path / "f32.zst")
        AdapterCodec.encode(F32_SAMPLE)
        assert AdapterCodec.dtype() == "F32"

        monkeypatch.setattr(AdapterCodec, "ZST", tmp_path / "bf16.zst")
        AdapterCodec.encode(BF16_SAMPLE)
        assert AdapterCodec.dtype() == "BF16"

    def test_rejects_mixed_dtype(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        import struct

        mixed_header = {
            "a": {"dtype": "F32", "shape": [4], "data_offsets": [0, 16]},
            "b": {"dtype": "BF16", "shape": [4], "data_offsets": [16, 24]},
        }
        header_json = orjson.dumps(mixed_header)
        mixed_bytes = (
            struct.pack("<Q", len(header_json))
            + header_json
            + b"\x00" * 24
        )
        mixed = tmp_path / "mixed.safetensors"
        mixed.write_bytes(mixed_bytes)
        monkeypatch.setattr(AdapterCodec, "ZST", tmp_path / "mixed.zst")
        with pytest.raises(AssertionError, match="homogeneous"):
            AdapterCodec.encode(mixed)


class TestShippedMetadata:
    def _skip_if_no_metadata(self) -> None:
        if not METADATA.exists():
            pytest.skip("metadata.json not shipped yet")

    def test_shipped_adapter_digest_matches_metadata(self) -> None:
        self._skip_if_no_metadata()
        meta = json.loads(METADATA.read_text())
        assert hashlib.sha256(AdapterCodec.ZST.read_bytes()).hexdigest() == meta["adapter_sha256"]

    def test_smoke_fixture_digest_matches_metadata(self) -> None:
        self._skip_if_no_metadata()
        if not SMOKE_EVAL.exists():
            pytest.skip("smoke fixture not shipped yet")
        meta = json.loads(METADATA.read_text())
        assert hashlib.sha256(SMOKE_EVAL.read_bytes()).hexdigest() == meta["smoke_eval_sha256"]

    def test_metadata_declares_dtype_matching_shipped_adapter(self) -> None:
        self._skip_if_no_metadata()
        meta = json.loads(METADATA.read_text())
        assert meta["adapter_dtype"] == AdapterCodec.dtype()


@pytest.mark.slow
@pytest.mark.skipif(not MLX_AVAILABLE, reason="requires mlx-lm extra")
class TestSmokeEvalAccuracy:
    EXACT_ACC_FLOOR: float = 0.90
    SESSION_RESUME_FLOOR: float = 1.0

    def _skip_if_incomplete(self) -> None:
        if not SMOKE_EVAL.exists():
            pytest.skip("smoke fixture not shipped yet")
        if not METADATA.exists():
            pytest.skip("metadata.json not shipped yet (no production adapter ready)")

    def _predict_all(self) -> list[tuple[int, int, str]]:
        from cc_sentiment.adapter import AdapterCodec as _Codec
        from cc_sentiment.engines.filter import FRUSTRATION_PATTERN
        from cc_sentiment.engines.protocol import DEMOS, SYSTEM_PROMPT
        from cc_sentiment.text import extract_score
        from mlx_lm import generate, load

        tmp = Path("/tmp/cc_sentiment_shipped_smoke")
        tmp.mkdir(exist_ok=True)
        (tmp / "adapters.safetensors").unlink(missing_ok=True)
        _Codec.decode(tmp / "adapters.safetensors")
        (tmp / "adapter_config.json").write_bytes(_Codec.CONFIG.read_bytes())

        model, tokenizer = load("unsloth/gemma-4-E2B-it-UD-MLX-4bit", adapter_path=str(tmp))

        results: list[tuple[int, int, str]] = []
        for line in SMOKE_EVAL.read_text().splitlines():
            if not line.strip():
                continue
            sample = orjson.loads(line)
            text = sample["text"]
            truth = int(sample["score"])
            tag = sample.get("tag", "")
            if FRUSTRATION_PATTERN.search(text):
                pred = 1
            else:
                messages = [{"role": "system", "content": SYSTEM_PROMPT}]
                for demo_msg, demo_score in DEMOS:
                    messages.append(
                        {"role": "user", "content": f"CONVERSATION:\nDEVELOPER: {demo_msg}"}
                    )
                    messages.append({"role": "assistant", "content": demo_score})
                messages.append(
                    {"role": "user", "content": f"CONVERSATION:\nDEVELOPER: {text.strip()}"}
                )
                prompt = tokenizer.apply_chat_template(
                    messages, tokenize=False, add_generation_prompt=True
                )
                out = generate(model, tokenizer, prompt=prompt, max_tokens=4, verbose=False)
                try:
                    pred = int(extract_score(out))
                except ValueError:
                    pred = 3
            results.append((truth, pred, tag))
        return results

    def test_smoke_eval_accuracy_floor(self) -> None:
        self._skip_if_incomplete()
        results = self._predict_all()
        correct = sum(1 for t, p, _ in results if t == p)
        acc = correct / len(results) if results else 0.0
        assert acc >= self.EXACT_ACC_FLOOR, f"smoke exact_acc {acc:.3f} < {self.EXACT_ACC_FLOOR}"

    def test_smoke_eval_session_resume_floor(self) -> None:
        self._skip_if_incomplete()
        results = [r for r in self._predict_all() if r[2] == "session_resume"]
        if not results:
            pytest.skip("smoke fixture has no session_resume samples")
        correct = sum(1 for t, p, _ in results if t == p)
        acc = correct / len(results)
        assert acc >= self.SESSION_RESUME_FLOOR, (
            f"session_resume tag-acc {acc:.3f} < {self.SESSION_RESUME_FLOOR}"
        )
