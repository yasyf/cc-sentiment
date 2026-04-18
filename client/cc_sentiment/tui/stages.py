from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Stage:
    pass


@dataclass(frozen=True)
class Booting(Stage):
    pass


@dataclass(frozen=True)
class Authenticating(Stage):
    pass


@dataclass(frozen=True)
class Discovering(Stage):
    pass


@dataclass(frozen=True)
class Scoring(Stage):
    total: int
    engine: str


@dataclass(frozen=True)
class Uploading(Stage):
    pass


@dataclass(frozen=True)
class IdleEmpty(Stage):
    pass


@dataclass(frozen=True)
class IdleCaughtUp(Stage):
    total_buckets: int
    total_sessions: int
    total_files: int


@dataclass(frozen=True)
class IdleAfterUpload(Stage):
    total_buckets: int
    total_sessions: int
    total_files: int


@dataclass(frozen=True)
class Error(Stage):
    message: str


@dataclass(frozen=True)
class RescanConfirm(Stage):
    prev: Stage
