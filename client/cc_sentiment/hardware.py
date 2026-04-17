from __future__ import annotations

import platform
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal, cast

ChipVariant = Literal["base", "Pro", "Max", "Ultra"]

BRAND_PATTERN = re.compile(r"^Apple M(\d+)(?: (Pro|Max|Ultra))?$")


@dataclass(frozen=True)
class HardwareProfile:
    family: int
    variant: ChipVariant
    p_cores: int
    memory_gb: int


class Hardware:
    BASELINE_OMLX_BUCKETS_PER_SEC: float = 113.0
    BASELINE_MLX_BUCKETS_PER_SEC: float = 0.0

    BANDWIDTH_GBPS: dict[tuple[int, ChipVariant], int] = {
        (1, "base"): 68,  (1, "Pro"): 200, (1, "Max"): 400, (1, "Ultra"): 800,
        (2, "base"): 100, (2, "Pro"): 200, (2, "Max"): 400, (2, "Ultra"): 800,
        (3, "base"): 100, (3, "Pro"): 150, (3, "Max"): 300,
        (4, "base"): 120, (4, "Pro"): 273, (4, "Max"): 546,
        (5, "Max"): 546,
    }
    BASELINE_BANDWIDTH_GBPS: int = 546
    MIN_MEMORY_GB: int = 16

    @staticmethod
    def read_brand() -> str:
        return subprocess.run(
            ["sysctl", "-n", "machdep.cpu.brand_string"],
            check=True, capture_output=True, text=True, timeout=2,
        ).stdout.strip()

    @staticmethod
    def read_p_cores() -> int:
        return int(subprocess.run(
            ["sysctl", "-n", "hw.perflevel0.physicalcpu"],
            check=True, capture_output=True, text=True, timeout=2,
        ).stdout.strip())

    @staticmethod
    def read_memory_gb() -> int:
        return int(subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            check=True, capture_output=True, text=True, timeout=2,
        ).stdout.strip()) // (1024 ** 3)

    @classmethod
    def detect_profile(cls) -> HardwareProfile | None:
        if sys.platform != "darwin" or platform.machine() != "arm64":
            return None
        try:
            brand = cls.read_brand()
            p_cores = cls.read_p_cores()
            memory_gb = cls.read_memory_gb()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, ValueError):
            return None
        match = BRAND_PATTERN.match(brand)
        if not match:
            return None
        family = int(match.group(1))
        variant = cast(ChipVariant, match.group(2) or "base")
        return HardwareProfile(family=family, variant=variant, p_cores=p_cores, memory_gb=memory_gb)

    @classmethod
    def estimate_buckets_per_sec(cls, engine: str) -> float | None:
        match engine:
            case "omlx": baseline = cls.BASELINE_OMLX_BUCKETS_PER_SEC
            case "mlx":  baseline = cls.BASELINE_MLX_BUCKETS_PER_SEC
            case _:      return None
        if baseline == 0.0:
            return None
        profile = cls.detect_profile()
        if profile is None or profile.memory_gb < cls.MIN_MEMORY_GB:
            return None
        bandwidth = cls.BANDWIDTH_GBPS.get((profile.family, profile.variant))
        if bandwidth is None:
            return None
        ratio = (bandwidth / cls.BASELINE_BANDWIDTH_GBPS) * (0.85 + 0.15 * profile.p_cores / 6)
        return baseline * ratio
