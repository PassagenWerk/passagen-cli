"""Ingestion commands: scan PDFs and run the full pipeline."""

import logging
from pathlib import Path
from typing import Annotated

import typer
from passagen.stages.running import run_pipeline
from passagen.stages.scanning import ScanDirectoryError, scan_directory

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def scan(
    ctx: typer.Context,
    directory: Annotated[Path, typer.Argument(help="Directory containing PDF files.")],
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Scan nested directories."),
    ] = True,
) -> None:
    settings = get_state(ctx).settings
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


def run_command(
    ctx: typer.Context,
    directory: Annotated[Path, typer.Argument(help="Directory containing PDF files.")],
    recursive: Annotated[
        bool,
        typer.Option("--recursive/--no-recursive", help="Scan nested directories."),
    ] = True,
) -> None:
    state = get_state(ctx)
    settings = state.settings
    try:
        with ConsoleProgress(console, "Starting Passagen pipeline...") as progress:
            result = run_pipeline(
                directory,
                database_path=settings.resolved_database_path,
                data_dir=settings.resolved_data_dir,
                providers=settings.providers,
                pipeline=settings.pipeline,
                recursive=recursive,
                provider_health=state.provider_health,
                execution_log_dir=state.execution_log_dir,
                progress=progress.update,
                llm_stats=state.llm_stats,
            )
    except ScanDirectoryError as exc:
        logger.error("run command failed during scan: %s", exc)
        console.print(f"[red]Run error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    for scan_failure in result.scan.failures:
        console.print(
            f"[red]Scan failed:[/red] {scan_failure.path}: {scan_failure.message}",
            highlight=False,
        )
    for update_failure in result.update.failures:
        console.print(
            f"[red]Update failed:[/red] {update_failure.paper_id}: {update_failure.message}",
            highlight=False,
        )
    console.print(
        f"Imported: {len(result.scan.imported)}, skipped: {len(result.scan.skipped)}, "
        f"scan failed: {len(result.scan.failures)}; "
        f"updated: {len(result.update.updated)}, update skipped: {len(result.update.skipped)}, "
        f"update failed: {len(result.update.failures)}"
    )
    if result.scan.failures or result.update.failures:
        raise typer.Exit(code=1)
