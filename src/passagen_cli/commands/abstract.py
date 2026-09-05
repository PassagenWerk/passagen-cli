import logging
from typing import Annotated

import typer
from passagen.config import ParserBackend
from passagen.stages.abstract_fixing import AbstractFixError, fix_paper_abstract
from passagen.stages.abstracts import AbstractBackfillError, backfill_abstracts
from passagen.storage.repository import DatabaseNotInitializedError, get_paper, list_papers

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def abstract_command(
    ctx: typer.Context,
    paper_id: Annotated[
        str | None,
        typer.Argument(help="Paper ID. Omit to process every paper."),
    ] = None,
    parser: Annotated[
        ParserBackend | None,
        typer.Option(help="Parser backend used when the author abstract is missing."),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(help="Refresh parser-provided originals and regenerate cleaned artifacts."),
    ] = False,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, "Preparing abstract stage...") as progress:
            extracted = backfill_abstracts(
                settings.resolved_database_path,
                settings.resolved_data_dir,
                settings.pipeline.parsing,
                settings.providers.grobid,
                paper_ids=[paper_id] if paper_id is not None else None,
                parser=parser,
                force=force,
                provider_health=state.provider_health,
                progress=progress.update,
            )
            if paper_id is not None:
                paper = get_paper(settings.resolved_database_path, paper_id)
                papers = [paper] if paper is not None else []
            else:
                papers = list_papers(settings.resolved_database_path)

            cleaned = 0
            cleaning_skipped = 0
            cleaning_failures: list[tuple[str, str]] = []
            candidates = [paper for paper in papers if paper.abstract]
            for index, paper in enumerate(candidates, start=1):
                progress.update(f"Paper {index}/{len(candidates)}: {paper.id}: cleaning abstract.")
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
                    cleaning_failures.append((paper.id, str(exc)))
                    continue
                if result.updated:
                    cleaned += 1
                else:
                    cleaning_skipped += 1
    except (DatabaseNotInitializedError, AbstractBackfillError) as exc:
        logger.error("abstract stage failed: target=%s error=%s", paper_id or "all", exc)
        console.print(f"[red]Abstract error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc

    for paper in extracted.missing:
        console.print(f"[yellow]No abstract found:[/yellow] {paper.id}", highlight=False)
    for failure in extracted.failures:
        console.print(f"[red]Extraction failed:[/red] {failure.paper_id}: {failure.message}")
    for failed_id, message in cleaning_failures:
        console.print(f"[red]Cleaning failed:[/red] {failed_id}: {message}", highlight=False)
    console.print(
        f"extracted: {len(extracted.updated)}, extraction skipped: {len(extracted.skipped)}, "
        f"not found: {len(extracted.missing)}, cleaned: {cleaned}, "
        f"cleaning skipped: {cleaning_skipped}, "
        f"failed: {len(extracted.failures) + len(cleaning_failures)}"
    )
    if extracted.failures or cleaning_failures:
        raise typer.Exit(code=1)
