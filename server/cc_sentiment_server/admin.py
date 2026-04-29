from __future__ import annotations

import asyncio

import modal
import typer
from rich.console import Console
from rich.table import Table

APP_NAME = "cc-sentiment"

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.callback()
def callback() -> None:
    """cc-sentiment operator tools."""


async def fetch_recent(limit: int) -> list[dict]:
    fn = modal.Function.from_name(APP_NAME, "list_recent_submissions")
    return await fn.remote.aio(limit)


@app.command()
def recent(limit: int = typer.Option(100, help="Max rows to fetch", min=1, max=1000)) -> None:
    submissions = asyncio.run(fetch_recent(limit))
    table = Table(title=f"Recent submissions ({len(submissions)})")
    for col in ("Identifier", "Sessions", "Records", "Avg", "Last upload", "First event", "Last event"):
        right = col in {"Sessions", "Records", "Avg"}
        table.add_column(col, no_wrap=True, justify="right" if right else "left")
    for s in submissions:
        table.add_row(
            f"{s['contributor_type']}:{s['contributor_id']}",
            f"{s['session_count']:,}",
            f"{s['record_count']:,}",
            f"{s['avg_score']:.2f}",
            s["last_uploaded"],
            s["earliest_event"],
            s["latest_event"],
        )
    Console().print(table)


def main() -> None:
    app()
