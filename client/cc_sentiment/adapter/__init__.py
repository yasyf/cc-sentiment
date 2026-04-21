from __future__ import annotations

import hashlib
import struct
from collections.abc import Callable
from pathlib import Path
from typing import ClassVar

import orjson

__all__ = ["AdapterCodec"]


class AdapterCodec:
    DIR: ClassVar[Path] = Path(__file__).parent
    ZST: ClassVar[Path] = DIR / "adapters.safetensors.zst"
    CONFIG: ClassVar[Path] = DIR / "adapter_config.json"
    F32_TYPESIZE: ClassVar[int] = 4
    COMPRESSION_LEVEL: ClassVar[int] = 19

    @classmethod
    def digest(cls) -> str:
        return hashlib.sha256(cls.ZST.read_bytes()).hexdigest()[:16]

    @classmethod
    def encode(cls, src: Path) -> None:
        import zstandard as zstd

        cls.ZST.write_bytes(
            zstd.ZstdCompressor(level=cls.COMPRESSION_LEVEL).compress(
                cls._walk(src.read_bytes(), cls._shuffle, require_f32=True)
            )
        )

    @classmethod
    def decode(cls, dst: Path) -> None:
        import zstandard as zstd

        dst.write_bytes(cls._walk(
            zstd.ZstdDecompressor().decompress(cls.ZST.read_bytes()),
            cls._unshuffle,
        ))

    @classmethod
    def _walk(
        cls,
        raw: bytes,
        transform: Callable[[bytes], bytes],
        *,
        require_f32: bool = False,
    ) -> bytes:
        header_end = 8 + struct.unpack("<Q", raw[:8])[0]
        body = raw[header_end:]
        out = bytearray(raw[:header_end])
        cursor = 0
        for name, meta in sorted(
            ((k, v) for k, v in orjson.loads(raw[8:header_end]).items() if k != "__metadata__"),
            key=lambda kv: kv[1]["data_offsets"][0],
        ):
            assert not require_f32 or meta["dtype"] == "F32", f"{name}: {meta['dtype']}"
            nbytes = meta["data_offsets"][1] - meta["data_offsets"][0]
            out.extend(transform(body[cursor : cursor + nbytes]))
            cursor += nbytes
        return bytes(out)

    @classmethod
    def _shuffle(cls, chunk: bytes) -> bytes:
        import numpy as np

        return np.frombuffer(chunk, dtype=np.uint8).reshape(-1, cls.F32_TYPESIZE).T.tobytes()

    @classmethod
    def _unshuffle(cls, chunk: bytes) -> bytes:
        import numpy as np

        return np.frombuffer(chunk, dtype=np.uint8).reshape(cls.F32_TYPESIZE, -1).T.tobytes()
