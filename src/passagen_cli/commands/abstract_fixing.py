"""Generate validated cleaned-abstract artifacts."""

import logging
from typing import Annotated

import typer
from passagen.stages.abstract_fixing import AbstractFixError, fix_paper_abstract
from passagen.storage.repository import DatabaseNotInitializedError, get_paper, list_papers

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def fix_abstracts_command(
    ctx: typer.Context,
    paper_id: Annotated[
        str | None,
        typer.Argument(help="Paper ID. Omit to process every paper with an abstract."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Regenerate cleaned abstracts even when cached artifacts are current."),
    ] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        if paper_id is not None:
            selected = get_paper(settings.resolved_database_path, paper_id)
            if selected is None:
                raise AbstractFixError(f"Paper not found: {paper_id}")
            papers = [selected]
        else:
            papers = [
                paper for paper in list_papers(settings.resolved_database_path) if paper.abstract
            ]
    except DatabaseNotInitializedError as exc:
        console.print(f"[red]Abstract cleaning error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    updated = 0
    skipped = 0
    failures: list[tuple[str, str]] = []
    with ConsoleProgress(console, "Preparing abstract cleaning...") as progress:
        for index, paper in enumerate(papers, start=1):
            progress.update(f"Paper {index}/{len(papers)}: {paper.id}")
            try:
                result = fix_paper_abstract(
                    settings.resolved_database_path,
                    settings.resolved_data_dir,
                    paper.id,
                    settings.providers.llm,
                    settings.pipeline.abstract_fixing,
                    provider_health=state.provider_health,
                    force=force,
                    execution_log_dir=state.execution_log_dir,
                    progress=progress.update,
                    llm_stats=state.llm_stats,
                )
            except AbstractFixError as exc:
                logger.warning("abstract cleaning failed: paper_id=%s error=%s", paper.id, exc)
                failures.append((paper.id, str(exc)))
                continue
            if result.updated:
                updated += 1
            else:
                skipped += 1

    for failed_id, message in failures:
        console.print(f"[red]Failed:[/red] {failed_id}: {message}", highlight=False)
    console.print(f"updated: {updated}, skipped: {skipped}, failed: {len(failures)}")
    if failures:
        raise typer.Exit(code=1)
