"""Backfill author abstracts without running generated stages."""

import logging
from typing import Annotated

import typer
from passagen.config import ParserBackend
from passagen.providers import check_parser_health
from passagen.stages.abstracts import AbstractBackfillError, backfill_abstracts
from passagen.storage.repository import DatabaseNotInitializedError

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def backfill_abstracts_command(
    ctx: typer.Context,
    paper_id: Annotated[
        str | None,
        typer.Argument(help="Paper ID. Omit to process every paper missing an abstract."),
    ] = None,
    parser: Annotated[
        ParserBackend | None,
        typer.Option(help="Parser backend: auto, grobid, or pymupdf."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Refresh existing parser-provided abstracts; user edits are preserved."),
    ] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    provider_health = state.provider_health
    selected_parser = parser or settings.pipeline.parsing.parser
    if selected_parser is not ParserBackend.PYMUPDF:
        provider_health = check_parser_health(settings.providers)
    try:
        with ConsoleProgress(console, "Preparing abstract backfill...") as progress:
            result = backfill_abstracts(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                settings.pipeline.parsing,
                settings.providers.grobid,
                paper_ids=[paper_id] if paper_id is not None else None,
                parser=parser,
                force=force,
                provider_health=provider_health,
                progress=progress.update,
            )
    except (DatabaseNotInitializedError, AbstractBackfillError) as exc:
        logger.error("abstract backfill failed: target=%s error=%s", paper_id or "all", exc)
        console.print(f"[red]Abstract backfill error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    for paper in result.updated:
        console.print(f"Abstract saved: {paper.id}: {paper.title or paper.original_filename}")
    for paper in result.missing:
        console.print(f"[yellow]No abstract found:[/yellow] {paper.id}", highlight=False)
    for failure in result.failures:
        console.print(f"[red]Failed:[/red] {failure.paper_id}: {failure.message}", highlight=False)
    console.print(
        f"updated: {len(result.updated)}, skipped: {len(result.skipped)}, "
        f"not found: {len(result.missing)}, failed: {len(result.failures)}"
    )
    if result.failures:
        raise typer.Exit(code=1)
