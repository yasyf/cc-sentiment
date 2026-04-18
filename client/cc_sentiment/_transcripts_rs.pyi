from typing import Any

def parse_line(line: str) -> dict[str, Any] | None: ...
def parse_file(path: str) -> list[dict[str, Any]]: ...
def bucket_keys_for(path: str) -> list[tuple[str, int]]: ...
def scan_bucket_keys(
    dir: str,
    *,
    name_contains: str | None = None,
    limit: int | None = None,
    known_mtimes: dict[str, float] | None = None,
) -> list[tuple[str, float, list[tuple[str, int]]]]: ...
def scan_parse_files(
    dir: str,
    *,
    name_contains: str | None = None,
    limit: int | None = None,
    known_mtimes: dict[str, float] | None = None,
) -> list[tuple[str, float, list[dict[str, Any]]]]: ...
def is_release_build() -> bool: ...
