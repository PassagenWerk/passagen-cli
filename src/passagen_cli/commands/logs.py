"""Execution log management sub-app."""

import typer

from passagen_cli.logging import archive_execution_logs
from passagen_cli.runtime import console, get_state

logs_app = typer.Typer(help="Manage Passagen execution logs.")


@logs_app.command(
    "clean",
    help="Move historical execution logs, except the current run, to logs/old.",
)
def logs_clean(ctx: typer.Context) -> None:
    state = get_state(ctx)
    moved = archive_execution_logs(exclude=(state.execution_log_dir,))
    console.print(f"Archived {len(moved)} execution log(s) to logs/old.")
