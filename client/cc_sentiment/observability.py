from __future__ import annotations

import os
from importlib.metadata import version
from importlib.util import find_spec
from typing import Any

import sentry_sdk
from sentry_sdk.integrations.typer import TyperIntegration


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
