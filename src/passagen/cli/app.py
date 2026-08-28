from __future__ import annotations

import logging
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from passagen import __version__
from passagen.cli.logging import (
    archive_execution_logs,
    configure_execution_logging,
    set_execution_log_level,
)
from passagen.config import ConfigError, ParserBackend, Settings, load_settings
from passagen.db import current_version, initialize_database
from passagen.models import PaperStatus
from passagen.providers import ProviderHealthSnapshot, check_provider_health
from passagen.repository import (
    DatabaseNotInitializedError,
    PaperRecord,
    get_artifact,
    get_paper,
    list_papers,
)
from passagen.stages.metadata import MetadataResolutionError, resolve_paper_metadata
from passagen.stages.outlining import OutlineError, outline_paper
from passagen.stages.parsing import PaperParsingError, parse_paper
from passagen.stages.scanning import ScanDirectoryError, scan_directory
from passagen.stages.summarization import SummaryError, summarize_paper
from passagen.stages.updating import UpdateTargetError, update_papers

app = typer.Typer(help="Manage paper PDFs and generate structured summaries.")
config_app = typer.Typer(help="Inspect Passagen configuration.")
db_app = typer.Typer(help="Manage the Passagen database.")
logs_app = typer.Typer(help="Manage Passagen execution logs.")
app.add_typer(config_app, name="config")
app.add_typer(db_app, name="db")
app.add_typer(logs_app, name="logs")
console = Console()
logger = logging.getLogger(__name__)


class AppState:
    def __init__(
        self,
        settings: Settings,
        execution_log_dir: Path,
        provider_health: ProviderHealthSnapshot,
    ) -> None:
        self.settings = settings
        self.execution_log_dir = execution_log_dir
        self.provider_health = provider_health


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
    execution_log_dir = configure_execution_logging(debug=bool(debug))
    command = ctx.invoked_subcommand or "passagen"
    logger.info("execution started: command=%s log=%s", command, execution_log_dir / "log.txt")
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
    provider_health = check_provider_health(settings.providers)
    for status in provider_health.statuses.values():
        log = logger.info if status.available else logger.warning
        log(
            "provider health: provider=%s available=%s detail=%s",
            status.name,
            status.available,
            status.detail,
        )
    ctx.call_on_close(lambda: logger.info("execution finished: command=%s", command))
    ctx.obj = AppState(settings, execution_log_dir, provider_health)


@config_app.command("check")
def config_check(ctx: typer.Context) -> None:
    settings = _state(ctx).settings
    logger.info("config check started")
    table = Table(show_header=False)
    table.add_row("data_dir", str(settings.resolved_data_dir))
    table.add_row("database_path", str(settings.resolved_database_path))
    table.add_row("debug", str(settings.debug).lower())
    table.add_row("providers.crossref", str(settings.providers.crossref.enabled).lower())
    table.add_row("providers.arxiv", str(settings.providers.arxiv.enabled).lower())
    table.add_row("providers.grobid.base_url", settings.providers.grobid.base_url)
    table.add_row("providers.llm.base_url", settings.providers.llm.base_url)
    table.add_row("providers.llm.model", settings.providers.llm.model)
    table.add_row(
        "providers.llm.disable_thinking", str(settings.providers.llm.disable_thinking).lower()
    )
    table.add_row("pipeline.metadata.first_pages", str(settings.pipeline.metadata.first_pages))
    table.add_row("pipeline.parsing.parser", settings.pipeline.parsing.parser.value)
    table.add_row(
        "pipeline.outlining.max_output_tokens",
        str(settings.pipeline.outlining.max_output_tokens),
    )
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


@logs_app.command("clean")
def logs_clean(ctx: typer.Context) -> None:
    state = _state(ctx)
    moved = archive_execution_logs(exclude=(state.execution_log_dir,))
    console.print(f"Archived {len(moved)} execution log(s) to logs/old.")


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
    force: Annotated[bool, typer.Option(help="Rebuild existing metadata.")] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, f"Resolving metadata for {paper_id}...") as progress:
            result = resolve_paper_metadata(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.pipeline.metadata,
                settings.providers,
                provider_health=_state(ctx).provider_health,
                force=force,
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
        console.print(f"Metadata already resolved for {paper_id}; use --force to rebuild.")


@app.command("update")
def update_command(
    ctx: typer.Context,
    paper_id: Annotated[
        str | None,
        typer.Argument(help="Paper ID. Omit to update every paper."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Rebuild every stage from metadata to the current target."),
    ] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, "Preparing update...") as progress:
            result = update_papers(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                settings.providers,
                settings.pipeline,
                paper_id,
                provider_health=_state(ctx).provider_health,
                execution_log_dir=_state(ctx).execution_log_dir,
                force=force,
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
        f"Target: {result.target_status.value}; "
        f"updated: {len(result.updated)}, "
        f"skipped: {len(result.skipped)}, "
        f"failed: {len(result.failures)}"
    )
    if result.failures:
        raise typer.Exit(code=1)


@app.command("parse")
def parse_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    parser: Annotated[
        ParserBackend | None,
        typer.Option(help="Parser backend: auto, grobid, or pymupdf."),
    ] = None,
    force: Annotated[bool, typer.Option(help="Rebuild an existing extracted artifact.")] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, f"Parsing full text for {paper_id}...") as progress:
            result = parse_paper(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.pipeline.parsing,
                settings.providers.grobid,
                provider_health=_state(ctx).provider_health,
                parser=parser,
                force=force,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, PaperParsingError) as exc:
        logger.error("parse command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Parse error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    for warning in result.warnings:
        console.print(f"[yellow]Warning:[/yellow] {warning}", highlight=False)
    if result.updated and result.artifact is not None and result.parsed is not None:
        console.print(
            f"Parsed {paper_id} with {result.parsed.parser}: "
            f"{len(result.parsed.sections)} sections, "
            f"{len(result.parsed.references)} references; artifact={result.artifact.path}",
            markup=False,
        )
    else:
        console.print(f"Paper {paper_id} is already parsed; use --force to rebuild.")


@app.command("summarize")
def summarize_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    force: Annotated[bool, typer.Option(help="Rebuild an existing summary.")] = False,
) -> None:
    settings = _state(ctx).settings
    try:
        with ConsoleProgress(console, f"Summarizing {paper_id}...") as progress:
            result = summarize_paper(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.providers.llm,
                settings.pipeline.summarization,
                provider_health=_state(ctx).provider_health,
                execution_log_dir=_state(ctx).execution_log_dir,
                force=force,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, SummaryError) as exc:
        logger.error("summarize command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Summary error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    if result.updated and result.artifact is not None and result.summary is not None:
        console.print(
            f"Structured summary saved for {paper_id}; artifact={result.artifact.path}",
            markup=False,
        )
    else:
        console.print(f"Paper {paper_id} is already summarized; use --force to rebuild.")


@app.command("outline")
def outline_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    force: Annotated[bool, typer.Option(help="Rebuild an existing Chinese outline.")] = False,
) -> None:
    state = _state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, f"Generating Chinese outline for {paper_id}...") as progress:
            result = outline_paper(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.providers.llm,
                settings.pipeline.outlining,
                provider_health=state.provider_health,
                execution_log_dir=state.execution_log_dir,
                force=force,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, OutlineError) as exc:
        logger.error("outline command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Outline error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    if result.updated and result.artifact is not None:
        console.print(
            f"Chinese outline saved for {paper_id}; artifact={result.artifact.path}",
            markup=False,
        )
    else:
        console.print(f"Paper {paper_id} is already outlined; use --force to rebuild.")


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
    extracted = get_artifact(settings.resolved_database_path, paper.id, "extracted_json")
    extracted_path = settings.resolved_data_dir / extracted.path if extracted is not None else None
    summary = get_artifact(settings.resolved_database_path, paper.id, "summary_json")
    summary_path = settings.resolved_data_dir / summary.path if summary is not None else None
    outline = get_artifact(settings.resolved_database_path, paper.id, "outline_zh_md")
    outline_path = settings.resolved_data_dir / outline.path if outline is not None else None
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
        ("extracted_path", str(extracted_path) if extracted_path is not None else "-"),
        ("summary_path", str(summary_path) if summary_path is not None else "-"),
        ("outline_path", str(outline_path) if outline_path is not None else "-"),
        ("imported_at", paper.imported_at),
    ]


def _state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    if not isinstance(state, AppState):
        raise RuntimeError("Application state is not initialized")
    return state
