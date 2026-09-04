"""Database administration sub-app."""

import logging
from datetime import datetime
from pathlib import Path
from typing import Annotated

import typer
from passagen.storage.database import backup_database, current_version, initialize_database

from passagen_cli.runtime import console, get_state

db_app = typer.Typer(help="Initialize, inspect, and back up the Passagen database.")
logger = logging.getLogger(__name__)


@db_app.command("init", help="Initialize the SQLite database without clearing existing data.")
def db_init(ctx: typer.Context) -> None:
    database_path = get_state(ctx).settings.resolved_database_path
    initialize_database(database_path)
    logger.info("database initialized: path=%s", database_path)
    console.print("Database initialized.")


@db_app.command("status", help="Show the current database schema version and initialization state.")
def db_status(ctx: typer.Context) -> None:
    database_path = get_state(ctx).settings.resolved_database_path
    version = current_version(database_path)
    if version is None:
        logger.error("database status failed: database is not initialized: path=%s", database_path)
        console.print("Database is not initialized.")
        raise typer.Exit(code=1)
    logger.info("database status: path=%s schema_version=%s", database_path, version)
    console.print(f"Database schema version: {version}")


@db_app.command(
    "backup",
    help="Create a consistent SQLite backup; managed artifact files are not copied.",
)
def db_backup(
    ctx: typer.Context,
    destination: Annotated[
        Path | None,
        typer.Argument(help="Backup file. Defaults to data_dir/backups/ with a timestamp."),
    ] = None,
) -> None:
    settings = get_state(ctx).settings
    target = destination or (
        settings.resolved_data_dir
        / "backups"
        / f"passagen-{datetime.now().strftime('%Y%m%d-%H%M%S-%f')}.db"
    )
    try:
        backup = backup_database(settings.resolved_database_path, target)
    except (FileNotFoundError, FileExistsError, ValueError) as exc:
        logger.error("database backup failed: %s", exc)
        console.print(f"[red]Backup error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    logger.info(
        "database backup created: source=%s target=%s",
        settings.resolved_database_path,
        backup,
    )
    console.print(f"Database backup created: {backup}", markup=False)
