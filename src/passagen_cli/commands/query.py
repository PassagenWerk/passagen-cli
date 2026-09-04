"""Read-only query commands: list papers and show paper details."""

import logging
from typing import Annotated

import typer
from passagen.config import Settings
from passagen.domain import PaperStatus
from passagen.storage.repository import (
    DatabaseNotInitializedError,
    PaperRecord,
    get_artifact,
    get_paper,
    list_papers,
)
from rich.table import Table

from passagen_cli.runtime import console, get_state

logger = logging.getLogger(__name__)


def list_command(
    ctx: typer.Context,
    status: Annotated[PaperStatus | None, typer.Option(help="Filter by paper status.")] = None,
) -> None:
    settings = get_state(ctx).settings
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


def show(ctx: typer.Context, paper_id: Annotated[str, typer.Argument(help="Paper ID.")]) -> None:
    settings = get_state(ctx).settings
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
    outline = get_artifact(settings.resolved_database_path, paper.id, "outline_md")
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
