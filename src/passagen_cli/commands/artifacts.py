"""Artifact integrity verification sub-app."""

import logging

import typer
from passagen.storage.maintenance import check_artifacts
from passagen.storage.repository import DatabaseNotInitializedError

from passagen_cli.runtime import console, get_state

artifacts_app = typer.Typer(help="Verify the integrity of managed artifacts.")
logger = logging.getLogger(__name__)


@artifacts_app.command(
    "check",
    help="Verify paths, file sizes, and SHA-256 hashes of registered artifacts.",
)
def artifacts_check(ctx: typer.Context) -> None:
    settings = get_state(ctx).settings
    try:
        result = check_artifacts(settings.resolved_database_path, settings.resolved_data_dir)
    except DatabaseNotInitializedError as exc:
        logger.error("artifact check failed: %s", exc)
        console.print(f"[red]Artifact check error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=1) from exc
    for issue in result.issues:
        console.print(
            f"[red]Invalid:[/red] {issue.artifact.kind} {issue.artifact.path}: {issue.message}",
            highlight=False,
        )
    console.print(f"Checked: {result.checked}; invalid: {len(result.issues)}")
    if result.issues:
        raise typer.Exit(code=1)
