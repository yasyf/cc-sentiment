from __future__ import annotations

import math
import re
import subprocess

MODEL_SIZE_BYTES = 2_500_000_000
KV_CACHE_PER_PROMPT_BYTES = 100_000_000
MEMORY_FRACTION = 0.5


class MemoryProbe:
    @staticmethod
    def total_memory() -> int:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())

    @staticmethod
    def page_size() -> int:
        result = subprocess.run(
            ["sysctl", "-n", "hw.pagesize"],
            capture_output=True,
            text=True,
        )
        return int(result.stdout.strip())

    @staticmethod
    def free_pages() -> int:
        result = subprocess.run(
            ["vm_stat"],
            capture_output=True,
            text=True,
        )
        output = result.stdout
        match = re.search(r"Pages free:\s+(\d+)", output)
        assert match, "Could not parse vm_stat output"
        free = int(match.group(1))
        speculative_match = re.search(r"Pages speculative:\s+(\d+)", output)
        speculative = int(speculative_match.group(1)) if speculative_match else 0
        return free + speculative

    @classmethod
    def available_memory(cls) -> int:
        return cls.free_pages() * cls.page_size()

    @classmethod
    def optimal_batch_size(cls) -> int:
        available = cls.available_memory()
        usable = available * MEMORY_FRACTION - MODEL_SIZE_BYTES
        return max(1, math.floor(usable / KV_CACHE_PER_PROMPT_BYTES))
