from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np
import orjson
import zstandard as zstd

F32_TYPESIZE = 4
ADAPTER_DIR = Path(__file__).resolve().parent.parent / "cc_sentiment" / "adapter"
SRC = ADAPTER_DIR / "adapters.safetensors"
DST = ADAPTER_DIR / "adapters.safetensors.zst"


def shuffle_body(body: bytes, offset: int, nbytes: int) -> bytes:
    arr = np.frombuffer(body[offset : offset + nbytes], dtype=np.uint8)
    return arr.reshape(-1, F32_TYPESIZE).T.tobytes()


def main() -> None:
    raw = SRC.read_bytes()
    hlen = struct.unpack("<Q", raw[:8])[0]
    header = orjson.loads(raw[8 : 8 + hlen])
    body = raw[8 + hlen :]

    out = bytearray(raw[: 8 + hlen])
    tensors = sorted(
        ((k, v) for k, v in header.items() if k != "__metadata__"),
        key=lambda kv: kv[1]["data_offsets"][0],
    )
    for name, meta in tensors:
        assert meta["dtype"] == "F32", f"{name} is {meta['dtype']}, not F32"
        off_s, off_e = meta["data_offsets"]
        out.extend(shuffle_body(body, off_s, off_e - off_s))

    compressed = zstd.ZstdCompressor(level=19).compress(bytes(out))
    DST.write_bytes(compressed)

    orig_mb = SRC.stat().st_size / (1024 * 1024)
    new_mb = DST.stat().st_size / (1024 * 1024)
    ratio = 100 * DST.stat().st_size / SRC.stat().st_size
    print(f"{SRC.name}: {orig_mb:.2f} MB", file=sys.stderr)
    print(f"{DST.name}: {new_mb:.2f} MB ({ratio:.1f}%)", file=sys.stderr)
    print(f"saved: {orig_mb - new_mb:.2f} MB", file=sys.stderr)


if __name__ == "__main__":
    main()
