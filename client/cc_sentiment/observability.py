from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from importlib.metadata import version
from importlib.util import find_spec
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.typer import TyperIntegration

from cc_sentiment.debug_log import DebugLog


class Crash:
    @classmethod
    def init(cls) -> None:
        if os.environ.get("CC_SENTIMENT_NO_TELEMETRY") == "1":
            return
        if not (dsn := os.environ.get("SENTRY_DSN") or cls.baked_dsn()):
            return
        sentry_sdk.init(
            dsn=dsn,
            release=f"cc-sentiment@{version('cc-sentiment')}",
            environment=os.environ.get("CC_SENTIMENT_ENV", "production"),
            send_default_pii=False,
            traces_sample_rate=0.0,
            profiles_sample_rate=0.0,
            auto_enabling_integrations=False,
            auto_session_tracking=False,
            max_breadcrumbs=20,
            ignore_errors=["KeyboardInterrupt", "SystemExit", "BrokenPipeError"],
            before_send=cls.before_send,
            integrations=[TyperIntegration()],
        )

    @classmethod
    def baked_dsn(cls) -> str:
        if find_spec("cc_sentiment._sentry_dsn") is None:
            return ""
        from cc_sentiment._sentry_dsn import DSN
        return DSN

    @classmethod
    def before_send(cls, event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
        if (exc_info := hint.get("exc_info")) is None:
            return event
        if exc_info[0].__module__.startswith("click.exceptions"):
            return None
        return event


class CrashReporter:
    @classmethod
    def capture(cls, error: BaseException, **tags: str) -> None:
        with sentry_sdk.new_scope() as scope:
            for k, v in tags.items():
                scope.set_tag(k, v)
            for cpe in cls.iter_called_process_errors(error):
                scope.set_context("subprocess", {
                    "cmd": list(cpe.cmd) if isinstance(cpe.cmd, (list, tuple)) else cpe.cmd,
                    "returncode": cpe.returncode,
                    "stdout": (cpe.output or b"").decode(errors="replace")[:4000],
                    "stderr": (cpe.stderr or b"").decode(errors="replace")[:4000],
                })
                scope.set_tag(
                    "subprocess.cmd0",
                    cpe.cmd[0] if isinstance(cpe.cmd, (list, tuple)) and cpe.cmd else str(cpe.cmd),
                )
            scope.set_context("cc_sentiment_log", {"tail": DebugLog.get().snapshot()})
            sentry_sdk.capture_exception(error)

    @staticmethod
    def iter_called_process_errors(
        exc: BaseException,
    ) -> Iterator[subprocess.CalledProcessError]:
        if isinstance(exc, subprocess.CalledProcessError):
            yield exc
        if isinstance(exc, BaseExceptionGroup):
            for sub in exc.exceptions:
                yield from CrashReporter.iter_called_process_errors(sub)
        if exc.__cause__ is not None:
            yield from CrashReporter.iter_called_process_errors(exc.__cause__)
        if exc.__context__ is not None and exc.__context__ is not exc.__cause__:
            yield from CrashReporter.iter_called_process_errors(exc.__context__)
