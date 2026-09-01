"""Batch update command: resume papers from their last successful stage."""

import logging
from typing import Annotated

import typer

from passagen.cli.runtime import ConsoleProgress, console, get_state
from passagen.stages.updating import UpdateTargetError, update_papers
from passagen.storage.repository import DatabaseNotInitializedError

logger = logging.getLogger(__name__)


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
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, "Preparing update...") as progress:
            result = update_papers(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                settings.providers,
                settings.pipeline,
                paper_id,
                provider_health=state.provider_health,
                execution_log_dir=state.execution_log_dir,
                force=force,
                progress=progress.update,
                llm_stats=state.llm_stats,
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
