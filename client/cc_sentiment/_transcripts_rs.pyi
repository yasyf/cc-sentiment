from typing import Any

class ParseStream:
    def recv(self) -> tuple[Any, ...] | None: ...
    def recv_many(self, max: int) -> list[tuple[Any, ...]]: ...

def stream_parse(paths: list[tuple[str, float]], prefetch: int) -> ParseStream: ...
def scan_bucket_keys(
    dir: str,
    *,
    name_contains: str | None = None,
    limit: int | None = None,
    known_mtimes: dict[str, float] | None = None,
) -> list[tuple[str, float, list[tuple[str, int]]]]: ...
