from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from passagen import __version__
from passagen.config import ConfigError, Settings, load_settings
from passagen.db import current_version, initialize_database

app = typer.Typer(help="Manage paper PDFs and generate structured summaries.")
config_app = typer.Typer(help="Inspect Passagen configuration.")
db_app = typer.Typer(help="Manage the Passagen database.")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
console = Console()


class AppState:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings


def version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option(help="Path to a YAML config file.")] = None,
    data_dir: Annotated[Path | None, typer.Option(help="Override the data directory.")] = None,
    debug: Annotated[bool | None, typer.Option(help="Enable debug output.")] = None,
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    del version
    try:
        settings = load_settings(config, {"data_dir": data_dir, "debug": debug})
    except ConfigError as exc:
        console.print(f"[red]Configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    ctx.obj = AppState(settings)


@config_app.command("check")
def config_check(ctx: typer.Context) -> None:
    settings = _state(ctx).settings
    table = Table(show_header=False)
    table.add_row("data_dir", str(settings.resolved_data_dir))
    table.add_row("database_path", str(settings.resolved_database_path))
    table.add_row("debug", str(settings.debug).lower())
    console.print(table)


@db_app.command("init")
def db_init(ctx: typer.Context) -> None:
    database_path = _state(ctx).settings.resolved_database_path
    initialize_database(database_path)
    console.print("Database initialized.")


@db_app.command("status")
def db_status(ctx: typer.Context) -> None:
    database_path = _state(ctx).settings.resolved_database_path
    version = current_version(database_path)
    if version is None:
        console.print("Database is not initialized.")
        raise typer.Exit(code=1)
    console.print(f"Database schema version: {version}")


def _state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    if not isinstance(state, AppState):
        raise RuntimeError("Application state is not initialized")
    return state
