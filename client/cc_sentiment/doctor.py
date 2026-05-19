from __future__ import annotations

import asyncio
import platform
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import anyio.to_thread
import httpx
import sentry_sdk
from rich.console import Console
from rich.table import Table

from cc_sentiment.debug_log import DebugLog
from cc_sentiment.engines import (
    ClaudeCLIEngine,
    ClaudeNotAuthenticated,
    ClaudeNotInstalled,
    ClaudeReady,
    EngineFactory,
)
from cc_sentiment.hardware import Hardware
from cc_sentiment.models import AppState
from cc_sentiment.repo import Repository
from cc_sentiment.upload import DEFAULT_SERVER_URL


@dataclass(frozen=True)
class ProbeOptions:
    claude: bool = True
    sentry: bool = True
    server: bool = True
    show_log: bool = True


class Doctor:
    @classmethod
    async def run(cls, options: ProbeOptions, console: Console) -> None:
        table = Table.grid(padding=(0, 2))
        table.add_column(style="bold")
        table.add_column()

        cls.add_platform_rows(table)
        cls.add_memory_rows(table)
        cls.add_engine_rows(table)
        cls.add_state_rows(table)
        await cls.add_db_rows(table)
        if options.claude:
            await cls.add_claude_probe_rows(table)
        if options.server:
            await cls.add_server_probe_rows(table)
        if options.sentry:
            cls.add_sentry_probe_rows(table)

        console.print(table)

        if options.show_log:
            console.rule("Recent log")
            snapshot = DebugLog.get().snapshot()
            console.print(snapshot if snapshot else "[dim](empty — nothing happened yet)[/]")

    @staticmethod
    def add_platform_rows(table: Table) -> None:
        profile = Hardware.detect_profile()
        match profile:
            case None:
                detail = f"{platform.system()} {platform.release()} {platform.machine()}"
            case _:
                detail = (
                    f"{platform.system()} {platform.release()} {platform.machine()} — "
                    f"Apple M{profile.family} {profile.variant}, {profile.p_cores} P-cores"
                )
        table.add_row("Platform", detail)
        table.add_row("Python", f"{sys.version.split()[0]}")

    @staticmethod
    def add_memory_rows(table: Table) -> None:
        total = Hardware.read_memory_gb()
        free = Hardware.read_free_memory_gb()
        threshold = Hardware.LOW_RAM_THRESHOLD_GB
        marker = f" [yellow]⚠ below {threshold} GB MLX threshold[/]" if free < threshold else ""
        table.add_row("Memory", f"{total} GB total, {free} GB available{marker}")

    @classmethod
    def add_engine_rows(cls, table: Table) -> None:
        default = EngineFactory.default()
        match ClaudeCLIEngine.check_status():
            case ClaudeReady():
                status, swap_eligible = "ClaudeReady ✓", True
            case ClaudeNotInstalled(brew_available=brew):
                status, swap_eligible = (
                    f"ClaudeNotInstalled (brew={'yes' if brew else 'no'})",
                    False,
                )
            case ClaudeNotAuthenticated():
                status, swap_eligible = "ClaudeNotAuthenticated", False
        resolved, reason = cls.resolve_with_reason(default, swap_eligible)
        line = f"default={default}  resolved={resolved}"
        if reason:
            line += f"  [dim]({reason})[/]"
        table.add_row("Engine", line)
        table.add_row("Claude CLI", status)

    @staticmethod
    def resolve_with_reason(default: str, swap_eligible: bool) -> tuple[str, str]:
        if default != "mlx":
            return default, ""
        if Hardware.read_free_memory_gb() >= Hardware.LOW_RAM_THRESHOLD_GB:
            return "mlx", ""
        if swap_eligible:
            return "claude", "auto-swap: low free RAM, Claude CLI ready"
        return "mlx", "low free RAM but Claude CLI not ready — MLX will OOM"

    @staticmethod
    def add_state_rows(table: Table) -> None:
        state_path = AppState.state_path()
        if not state_path.exists():
            table.add_row("State", "[yellow]missing — run cc-sentiment setup[/]")
            return
        config = AppState.load().config
        if config is None:
            table.add_row("State", f"{state_path} present but unconfigured")
            return
        table.add_row(
            "State",
            f"{state_path} — contributor_type={config.contributor_type}, key_type={config.key_type}",
        )

    @classmethod
    async def add_db_rows(cls, table: Table) -> None:
        db_path = Repository.default_path()
        if not db_path.exists():
            table.add_row("Records DB", f"{db_path} — not created yet")
            return
        records, sessions, files = await anyio.to_thread.run_sync(cls.read_db_stats, db_path)
        table.add_row(
            "Records DB",
            f"{db_path} — {records:,} records, {sessions:,} sessions, {files:,} files",
        )

    @staticmethod
    def read_db_stats(db_path: Path) -> tuple[int, int, int]:
        with Repository.open(db_path) as repo:
            return repo.stats()

    @classmethod
    async def add_claude_probe_rows(cls, table: Table) -> None:
        if not isinstance(ClaudeCLIEngine.check_status(), ClaudeReady):
            table.add_row("Claude probe", "[yellow]skipped — Claude CLI not ready[/]")
            return
        engine = ClaudeCLIEngine(ClaudeCLIEngine.HAIKU_MODEL, verbose=True)
        argv = engine.argv([{"role": "user", "content": "Reply with the word ping."}])
        start = time.monotonic()
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr, rc = await engine._collect(proc)
        elapsed_ms = (time.monotonic() - start) * 1000
        rc_marker = "[green]✓[/]" if rc == 0 else "[red]✗[/]"
        table.add_row(
            "Claude probe",
            f"{rc_marker} exit {rc}, {elapsed_ms:.0f} ms, "
            f"stdout {len(stdout)} B, stderr {len(stderr)} B",
        )
        if rc != 0 or len(stdout) < 200:
            table.add_row("  stdout", cls.short(stdout))
            table.add_row("  stderr", cls.short(stderr))

    @staticmethod
    async def add_server_probe_rows(table: Table) -> None:
        url = DEFAULT_SERVER_URL
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(url)
        except httpx.HTTPError as e:
            elapsed_ms = (time.monotonic() - start) * 1000
            table.add_row("Server", f"[red]{type(e).__name__}: {e}[/] ({elapsed_ms:.0f} ms)")
            return
        elapsed_ms = (time.monotonic() - start) * 1000
        marker = "[green]✓[/]" if response.status_code < 500 else "[red]✗[/]"
        table.add_row("Server", f"{marker} {url} — HTTP {response.status_code} ({elapsed_ms:.0f} ms)")

    @staticmethod
    def add_sentry_probe_rows(table: Table) -> None:
        if not sentry_sdk.get_client().is_active():
            table.add_row("Sentry", "[yellow]not configured (no DSN baked, no SENTRY_DSN env)[/]")
            return
        DebugLog.get().append("doctor", "cc-sentiment debug probe event")
        with sentry_sdk.new_scope() as scope:
            scope.set_tag("source", "doctor")
            scope.set_context("cc_sentiment_log", {"tail": DebugLog.get().snapshot()})
            event_id = sentry_sdk.capture_message("cc-sentiment debug probe", level="info")
        table.add_row(
            "Sentry",
            f"[green]✓[/] sent probe event {event_id or '(no id)'}",
        )

    @staticmethod
    def short(payload: bytes) -> str:
        decoded = payload.decode(errors="replace").strip()
        if not decoded:
            return "[dim](empty)[/]"
        if len(decoded) > 400:
            return decoded[:400] + " [dim]…(truncated)[/]"
        return decoded
