from __future__ import annotations

import sys

from cc_sentiment.adapter import AdapterCodec


def main() -> None:
    src = AdapterCodec.DIR / "adapters.safetensors"
    AdapterCodec.encode(src)
    orig = src.stat().st_size
    new = AdapterCodec.ZST.stat().st_size
    print(
        f"{orig / 1024**2:.2f} MB \u2192 {new / 1024**2:.2f} MB "
        f"({100 * new / orig:.1f}%)",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
