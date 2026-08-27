from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from passagen import __version__
from passagen.config import ConfigError, Settings, load_settings
from passagen.db import current_version, initialize_database
from passagen.execution_logging import configure_execution_logging, set_execution_log_level
from passagen.metadata_service import MetadataResolutionError, resolve_paper_metadata
from passagen.models import PaperStatus
from passagen.repository import (
    DatabaseNotInitializedError,
    PaperRecord,
    get_paper,
    list_papers,
)
from passagen.scanning import ScanDirectoryError, scan_directory
from passagen.updating import (
    LATEST_IMPLEMENTED_STATUS,
    UpdateTargetError,
    update_papers,
)

app = typer.Typer(help="Manage paper PDFs and generate structured summaries.")
config_app = typer.Typer(help="Inspect Passagen configuration.")
db_app = typer.Typer(help="Manage the Passagen database.")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
console = Console()
logger = logging.getLogger(__name__)


class AppState:
    def __init__(self, settings: Settings, log_path: Path) -> None:
        self.settings = settings
        self.log_path = log_path


class ConsoleProgress:
    def __init__(self, output: Console, initial_message: str) -> None:
        self.output = output
        self.initial_message = initial_message
        self.status: Status | None = None

    def __enter__(self) -> ConsoleProgress:
        if self.output.is_terminal:
            self.status = self.output.status(self.initial_message)
            self.status.start()
        else:
            self.output.print(self.initial_message, markup=False)
        return self

    def update(self, message: str) -> None:
        if self.status is not None:
            self.status.update(message)
        else:
            self.output.print(message, markup=False)

    def __exit__(self, *_args: object) -> None:
        if self.status is not None:
            self.status.stop()


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
    log_path = configure_execution_logging(debug=bool(debug))
    command = ctx.invoked_subcommand or "passagen"
    logger.info("execution started: command=%s log=%s", command, log_path)
    try:
        settings = load_settings(config, {"data_dir": data_dir, "debug": debug})
    except ConfigError as exc:
        logger.error("configuration failed: %s", exc)
        console.print(f"[red]Configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    set_execution_log_level(debug=settings.debug)
    logger.info(
        "configuration loaded: data_dir=%s database=%s debug=%s",
        settings.resolved_data_dir,
        settings.resolved_database_path,
        settings.debug,
    )
    ctx.call_on_close(lambda: logger.info("execution finished: command=%s", command))
    ctx.obj = AppState(settings, log_path)


@config_app.command("check")
def config_check(ctx: typer.Context) -> None:
    settings = _state(ctx).settings
    logger.info("config check started")
    table = Table(show_header=False)
    table.add_row("data_dir", str(settings.resolved_data_dir))
    table.add_row("database_path", str(settings.resolved_database_path))
    table.add_row("debug", str(settings.debug).lower())
    table.add_row("metadata.first_pages", str(settings.metadata.first_pages))
    table.add_row("metadata.crossref", str(settings.metadata.crossref.enabled).lower())
    table.add_row("metadata.arxiv", str(settings.metadata.arxiv.enabled).lower())
    console.print(table)


@db_app.command("init")
def db_init(ctx: typer.Context) -> None:
    database_path = _state(ctx).settings.resolved_database_path
    initialize_database(database_path)
    logger.info("database initialized: path=%s", database_path)
    console.print("Database initialized.")


@db_app.command("status")
def db_status(ctx: typer.Context) -> None:
    database_path = _state(ctx).settings.resolved_database_path
    version = current_version(database_path)
    if version is None:
        logger.error("database status failed: database is not initialized: path=%s", database_path)
        console.print("Database is not initialized.")
        raise typer.Exit(code=1)
    logger.info("database status: path=%s schema_version=%s", database_path, version)
    console.print(f"Database schema version: {version}")


@app.command("scan")
def scan(
    ctx: typer.Context,
    directory: Annotated[Path, typer.Argument(help="Directory containing PDF files.")],
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Scan nested directories."),
    ] = True,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, "Starting PDF scan...") as progress:
            result = scan_directory(
                directory,
                data_dir=settings.resolved_data_dir,
                database_path=settings.resolved_database_path,
                recursive=recursive,
                progress=progress.update,
            )
    except ScanDirectoryError as exc:
        logger.error("scan command failed: %s", exc)
        console.print(f"[red]Scan error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc

    for failure in result.failures:
        console.print(
            f"[red]Failed:[/red] {failure.path}: {failure.message}",
            highlight=False,
        )
    console.print(
        f"Imported: {len(result.imported)}, "
        f"skipped: {len(result.skipped)}, "
        f"failed: {len(result.failures)}"
    )
    if result.failures:
        raise typer.Exit(code=1)


@app.command("list")
def list_command(
    ctx: typer.Context,
    status: Annotated[PaperStatus | None, typer.Option(help="Filter by paper status.")] = None,
) -> None:
    settings = _state(ctx).settings
    try:
        papers = list_papers(settings.resolved_database_path, status)
    except DatabaseNotInitializedError as exc:
        logger.error("list command failed: %s", exc)
        console.print(f"[red]Database error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    table = Table()
    table.add_column("ID", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Filename")
    table.add_column("Title")
    for paper in papers:
        table.add_row(
            paper.id,
            paper.status.value,
            paper.original_filename,
            paper.title or "-",
        )
    console.print(table)


@app.command("metadata")
def metadata_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    refresh: Annotated[bool, typer.Option(help="Refresh already resolved metadata.")] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, f"Resolving metadata for {paper_id}...") as progress:
            result = resolve_paper_metadata(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.metadata,
                refresh=refresh,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, MetadataResolutionError) as exc:
        logger.error("metadata command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Metadata error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}", highlight=False)
    if result.updated:
        console.print(f"Metadata resolved for {paper_id}.")
    else:
        console.print(f"Metadata already resolved for {paper_id}; use --refresh to update.")


@app.command("update")
def update_command(
    ctx: typer.Context,
    paper_id: Annotated[
        str | None,
        typer.Argument(help="Paper ID. Omit to update every paper."),
    ] = None,
    refresh: Annotated[
        bool,
        typer.Option(help="Refresh already completed stages."),
    ] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, "Preparing update...") as progress:
            result = update_papers(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                settings.metadata,
                paper_id,
                refresh=refresh,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, UpdateTargetError) as exc:
        logger.error("update command failed: target=%s error=%s", paper_id or "all", exc)
        console.print(f"[red]Update error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    for paper in result.updated:
        console.print(
            f"Updated {paper.id} to {paper.status.value}: {paper.title or paper.original_filename}",
            markup=False,
        )
    for warning in result.warnings:
        console.print(
            f"[yellow]Warning:[/yellow] {warning.paper_id}: {warning.message}",
            highlight=False,
        )
    for failure in result.failures:
        console.print(
            f"[red]Failed:[/red] {failure.paper_id}: {failure.message}",
            highlight=False,
        )
    console.print(
        f"Target: {LATEST_IMPLEMENTED_STATUS.value}; "
        f"updated: {len(result.updated)}, "
        f"skipped: {len(result.skipped)}, "
        f"failed: {len(result.failures)}"
    )
    if result.failures:
        raise typer.Exit(code=1)


@app.command("show")
def show(ctx: typer.Context, paper_id: Annotated[str, typer.Argument(help="Paper ID.")]) -> None:
    settings = _state(ctx).settings
    try:
        paper = get_paper(settings.resolved_database_path, paper_id)
    except DatabaseNotInitializedError as exc:
        logger.error("show command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Database error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    if paper is None:
        logger.error("show command failed: paper not found: paper_id=%s", paper_id)
        console.print(f"[red]Paper not found:[/red] {paper_id}", highlight=False)
        raise typer.Exit(code=1)

    for name, value in _paper_details(settings, paper):
        console.print(f"{name}: {value}", markup=False, soft_wrap=True)


def _paper_details(settings: Settings, paper: PaperRecord) -> list[tuple[str, str]]:
    managed_path = (
        settings.resolved_data_dir / paper.managed_pdf_path
        if paper.managed_pdf_path is not None
        else None
    )
    return [
        ("id", paper.id),
        ("status", paper.status.value),
        ("title", paper.title or "-"),
        ("authors", "; ".join(paper.authors) if paper.authors else "-"),
        ("year", str(paper.year) if paper.year is not None else "-"),
        ("venue", paper.venue or "-"),
        ("doi", paper.doi or "-"),
        ("arxiv_id", paper.arxiv_id or "-"),
        ("source_url", paper.source_url or "-"),
        (
            "metadata_sources",
            ", ".join(
                f"{field}={source}" for field, source in sorted(paper.metadata_sources.items())
            )
            or "-",
        ),
        ("original_filename", paper.original_filename),
        ("pdf_sha256", paper.pdf_sha256),
        (
            "file_size_bytes",
            str(paper.file_size_bytes) if paper.file_size_bytes is not None else "-",
        ),
        ("managed_pdf_path", str(managed_path) if managed_path is not None else "-"),
        ("imported_at", paper.imported_at),
    ]


def _state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    if not isinstance(state, AppState):
        raise RuntimeError("Application state is not initialized")
    return state
