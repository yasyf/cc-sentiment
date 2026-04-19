from __future__ import annotations

from unittest.mock import patch

import pytest

from cc_sentiment.hardware import Hardware


class TestDetectProfile:
    @pytest.mark.parametrize(
        "brand,expected_family,expected_variant",
        [
            ("Apple M3 Max", 3, "Max"),
            ("Apple M1", 1, "base"),
            ("Apple M2 Ultra", 2, "Ultra"),
            ("Apple M5 Max", 5, "Max"),
            ("Apple M4 Pro", 4, "Pro"),
        ],
    )
    def test_parse_brand_extracts_family_and_variant(
        self, brand: str, expected_family: int, expected_variant: str
    ) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
             patch.object(Hardware, "read_brand", return_value=brand), \
             patch.object(Hardware, "read_p_cores", return_value=6), \
             patch.object(Hardware, "read_memory_gb", return_value=32):
            profile = Hardware.detect_profile()
        assert profile is not None
        assert profile.family == expected_family
        assert profile.variant == expected_variant

    @pytest.mark.parametrize("brand", ["Intel Xeon", "Apple Silicon", "", "Apple M"])
    def test_parse_brand_returns_none_on_garbage(self, brand: str) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
             patch.object(Hardware, "read_brand", return_value=brand), \
             patch.object(Hardware, "read_p_cores", return_value=6), \
             patch.object(Hardware, "read_memory_gb", return_value=32):
            assert Hardware.detect_profile() is None

    def test_detect_returns_none_on_non_apple_silicon(self) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "linux"):
            assert Hardware.detect_profile() is None
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="x86_64"):
            assert Hardware.detect_profile() is None


class TestEstimateBucketsPerSec:
    def test_estimate_returns_none_on_unknown_chip(self) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
             patch.object(Hardware, "read_brand", return_value="Apple M99 Pro"), \
             patch.object(Hardware, "read_p_cores", return_value=6), \
             patch.object(Hardware, "read_memory_gb", return_value=32):
            assert Hardware.estimate_buckets_per_sec("omlx") is None

    def test_estimate_returns_none_for_claude_engine(self) -> None:
        assert Hardware.estimate_buckets_per_sec("claude") is None

    def test_estimate_returns_none_when_memory_below_16gb(self) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
             patch.object(Hardware, "read_brand", return_value="Apple M1"), \
             patch.object(Hardware, "read_p_cores", return_value=4), \
             patch.object(Hardware, "read_memory_gb", return_value=8):
            assert Hardware.estimate_buckets_per_sec("omlx") is None

    def test_estimate_returns_none_when_msgs_per_sec_is_zero(self) -> None:
        with patch.object(Hardware, "BASELINE_OMLX_USER_MSGS_PER_SEC", 0.0):
            assert Hardware.estimate_buckets_per_sec("omlx") is None

    def test_estimate_returns_none_when_avg_msgs_per_bucket_is_zero(self) -> None:
        with patch.object(Hardware, "AVG_NON_FILTERED_USER_MSGS_PER_BUCKET", 0.0):
            assert Hardware.estimate_buckets_per_sec("omlx") is None

    def test_estimate_returns_none_for_mlx_when_baseline_unfilled(self) -> None:
        assert Hardware.BASELINE_MLX_USER_MSGS_PER_SEC == 0.0
        assert Hardware.estimate_buckets_per_sec("mlx") is None

    def test_estimate_scales_with_chip_family(self) -> None:
        def rate_for(brand: str, cores: int, memory: int = 32) -> float | None:
            with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
                 patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
                 patch.object(Hardware, "read_brand", return_value=brand), \
                 patch.object(Hardware, "read_p_cores", return_value=cores), \
                 patch.object(Hardware, "read_memory_gb", return_value=memory), \
                 patch.object(Hardware, "BASELINE_OMLX_USER_MSGS_PER_SEC", 10.0), \
                 patch.object(Hardware, "AVG_NON_FILTERED_USER_MSGS_PER_BUCKET", 1.0):
                return Hardware.estimate_buckets_per_sec("omlx")

        m5_max = rate_for("Apple M5 Max", 6, memory=128)
        m3_max = rate_for("Apple M3 Max", 6)
        m3_pro = rate_for("Apple M3 Pro", 6)
        m2_ultra = rate_for("Apple M2 Ultra", 8, memory=64)

        assert m5_max == pytest.approx(10.0)
        assert m3_pro is not None and m3_max is not None and m2_ultra is not None
        assert m3_pro < m3_max < m2_ultra

    def test_estimate_returns_value_for_baseline_hardware(self) -> None:
        with patch("cc_sentiment.hardware.sys.platform", "darwin"), \
             patch("cc_sentiment.hardware.platform.machine", return_value="arm64"), \
             patch.object(Hardware, "read_brand", return_value="Apple M5 Max"), \
             patch.object(Hardware, "read_p_cores", return_value=6), \
             patch.object(Hardware, "read_memory_gb", return_value=128):
            rate = Hardware.estimate_buckets_per_sec("omlx")
        assert rate == pytest.approx(
            Hardware.BASELINE_OMLX_USER_MSGS_PER_SEC / Hardware.AVG_NON_FILTERED_USER_MSGS_PER_BUCKET
        )
