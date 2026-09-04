"""Single-paper pipeline stage commands: metadata, parse, summarize, and outline."""

import logging
from typing import Annotated

import typer
from passagen.config import ParserBackend
from passagen.stages.metadata import MetadataResolutionError, resolve_paper_metadata
from passagen.stages.outlining import OutlineError, outline_paper
from passagen.stages.parsing import PaperParsingError, parse_paper
from passagen.stages.summarization import SummaryError, summarize_paper
from passagen.storage.repository import DatabaseNotInitializedError

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def metadata_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    force: Annotated[bool, typer.Option(help="Rebuild existing metadata.")] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, f"Resolving metadata for {paper_id}...") as progress:
            result = resolve_paper_metadata(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.pipeline.metadata,
                settings.providers,
                provider_health=state.provider_health,
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


def parse_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    parser: Annotated[
        ParserBackend | None,
        typer.Option(help="Parser backend: auto, grobid, or pymupdf."),
    ] = None,
    force: Annotated[bool, typer.Option(help="Rebuild an existing extracted artifact.")] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, f"Parsing full text for {paper_id}...") as progress:
            result = parse_paper(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.pipeline.parsing,
                settings.providers.grobid,
                provider_health=state.provider_health,
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


def summarize_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    force: Annotated[bool, typer.Option(help="Rebuild an existing summary.")] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, f"Summarizing {paper_id}...") as progress:
            result = summarize_paper(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                paper_id,
                settings.providers.llm,
                settings.pipeline.summarization,
                provider_health=state.provider_health,
                execution_log_dir=state.execution_log_dir,
                force=force,
                progress=progress.update,
                llm_stats=state.llm_stats,
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


def outline_command(
    ctx: typer.Context,
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
    force: Annotated[bool, typer.Option(help="Rebuild an existing English outline.")] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, f"Generating English outline for {paper_id}...") as progress:
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
                llm_stats=state.llm_stats,
            )
    except (DatabaseNotInitializedError, OutlineError) as exc:
        logger.error("outline command failed: paper_id=%s error=%s", paper_id, exc)
        console.print(f"[red]Outline error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    if result.updated and result.artifact is not None:
        console.print(
            f"English outline saved for {paper_id}; artifact={result.artifact.path}",
            markup=False,
        )
    else:
        console.print(f"Paper {paper_id} is already outlined; use --force to rebuild.")
