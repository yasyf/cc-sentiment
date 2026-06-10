from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from spawnllm.mlx import AdapterCodec as SpawnllmAdapterCodec


class AdapterCodec(SpawnllmAdapterCodec):
    DIR: ClassVar[Path] = Path(__file__).parent
    ZST: ClassVar[Path] = DIR / "adapters.safetensors.zst"
    CONFIG: ClassVar[Path] = DIR / "adapter_config.json"
