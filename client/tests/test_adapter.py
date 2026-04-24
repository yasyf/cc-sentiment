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
        from cc_sentiment.engines.filter import FrustrationFilter
        from cc_sentiment.sentiment import SentimentClassifier

        classifier = SentimentClassifier()
        samples = [
            orjson.loads(line) for line in SMOKE_EVAL.read_text().splitlines() if line.strip()
        ]
        non_filter_indices = [
            i for i, s in enumerate(samples) if not FrustrationFilter.matches_text(s["text"])
        ]
        contents = [
            f"CONVERSATION:\nDEVELOPER: {samples[i]['text'].strip()}"
            for i in non_filter_indices
        ]
        scores = classifier.score_user_contents(contents) if contents else []
        score_by_index = dict(zip(non_filter_indices, scores, strict=True))

        return [
            (int(s["score"]), score_by_index.get(i, 1), s.get("tag", ""))
            for i, s in enumerate(samples)
        ]

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
