from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from cc_sentiment.adapter import AdapterCodec

FIXTURES: Path = Path(__file__).parent / "fixtures"
F32_SAMPLE: Path = FIXTURES / "adapter_f32_sample.safetensors"
BF16_SAMPLE: Path = FIXTURES / "adapter_bf16_sample.safetensors"


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

        import orjson

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
