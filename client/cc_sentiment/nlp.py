from __future__ import annotations

import asyncio
import contextlib
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

import anyio.to_thread

if TYPE_CHECKING:
    import spacy.language

MODEL_NAME = "en_core_web_sm"
DISABLED_PIPES = ["parser"]
SPACY_CACHE_DIR = Path.home() / ".cache" / "spacy"
SPACY_MODEL_VERSION = "3.8.0"


class NLP:
    model: ClassVar[spacy.language.Language | None] = None
    failed: ClassVar[bool] = False
    last_download_output: ClassVar[str | None] = None
    locks_by_loop: ClassVar[dict[int, asyncio.Lock]] = {}

    @classmethod
    def get(cls) -> spacy.language.Language | None:
        return cls.model

    @classmethod
    async def ensure_ready(cls) -> spacy.language.Language | None:
        if cls.model is not None:
            return cls.model
        if cls.failed:
            return None
        loop_id = id(asyncio.get_running_loop())
        lock = cls.locks_by_loop.setdefault(loop_id, asyncio.Lock())
        async with lock:
            if cls.model is None and not cls.failed:
                try:
                    cls.model = await anyio.to_thread.run_sync(cls.load_or_download)
                except (OSError, subprocess.CalledProcessError, ImportError, RuntimeError) as exc:
                    cls.failed = True
                    cls.last_download_output = cls.format_failure(exc)
        return cls.model

    @staticmethod
    def format_failure(exc: BaseException) -> str:
        match exc:
            case subprocess.CalledProcessError():
                out = (exc.stdout or "").strip()
                err = (exc.stderr or "").strip()
                return f"exit={exc.returncode} stdout={out!r} stderr={err!r}"
            case _:
                return f"{exc.__class__.__name__}: {exc}"

    @staticmethod
    def load_or_download() -> spacy.language.Language:
        import spacy

        cache_str = str(SPACY_CACHE_DIR)
        if (SPACY_CACHE_DIR / MODEL_NAME).is_dir() and cache_str not in sys.path:
            sys.path.insert(0, cache_str)

        with contextlib.suppress(OSError):
            return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)

        SPACY_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                sys.executable, "-m", "pip", "install",
                "--target", cache_str,
                "--quiet", "--upgrade",
                f"{MODEL_NAME}=={SPACY_MODEL_VERSION}",
            ],
            check=True, capture_output=True, text=True,
        )
        if cache_str not in sys.path:
            sys.path.insert(0, cache_str)
        return spacy.load(MODEL_NAME, disable=DISABLED_PIPES)
