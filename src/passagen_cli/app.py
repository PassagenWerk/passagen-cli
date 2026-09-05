"""Typer composition root: application callback and command registration."""

from __future__ import annotations

import logging
from importlib.metadata import version
from pathlib import Path
from typing import Annotated

import typer
from passagen.config import ConfigError, load_settings
from passagen.providers import LlmCallStats, ProviderHealthSnapshot, check_provider_health

from passagen_cli.commands import (
    abstract_fixing,
    abstracts,
    artifacts,
    config,
    db,
    health,
    ingestion,
    logs,
    organization,
    query,
    stages,
    update,
)
from passagen_cli.logging import configure_execution_logging, set_execution_log_level
from passagen_cli.runtime import AppState, console, finish_execution

app = typer.Typer(help="Manage paper PDFs and generate validated English research artifacts.")

app.command(
    "scan",
    help="Import PDFs from a directory into managed storage with SHA-256 deduplication.",
)(ingestion.scan)
app.command(
    "run",
    help="Scan a directory and advance all pending papers to the outlined state.",
)(ingestion.run_command)
app.command("list", help="List papers, optionally filtered by their last successful stage.")(
    query.list_command
)
app.command(
    "metadata",
    help="Extract local PDF metadata and enrich it with configured metadata providers.",
)(stages.metadata_command)
app.command(
    "update",
    help=(
        "Resume one or all papers from their last successful stage; --force rebuilds from metadata."
    ),
)(update.update_command)
app.command(
    "parse",
    help="Parse full text into extracted.json with GROBID, PyMuPDF, or automatic selection.",
)(stages.parse_command)
app.command(
    "summarize",
    help="Generate and validate the general English Structured Summary v2.",
)(stages.summarize_command)
app.command(
    "outline",
    help="Generate a hierarchical English technical outline from validated summary.json only.",
)(stages.outline_command)
app.command(
    "backfill-abstracts",
    help="Extract missing author abstracts without changing status or generated artifacts.",
)(abstracts.backfill_abstracts_command)
app.command(
    "fix-abstracts",
    help="Create validated LLM-cleaned views of author abstracts.",
)(abstract_fixing.fix_abstracts_command)
app.command("show", help="Show paper metadata, last successful stage, and managed artifact paths.")(
    query.show
)
app.command("check", help="Check external service reachability and print the results.")(
    health.check_command
)
app.add_typer(config.config_app, name="config")
app.add_typer(db.db_app, name="db")
app.add_typer(logs.logs_app, name="logs")
app.add_typer(artifacts.artifacts_app, name="artifacts")
app.add_typer(organization.collection_app, name="collection")
app.add_typer(organization.tag_app, name="tag")

logger = logging.getLogger(__name__)

__version__ = version("passagen-cli")


def version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def main(
    ctx: typer.Context,
    config: Annotated[
        Path | None,
        typer.Option(help="Path to a YAML config file (default: <data-dir>/passagen.yaml)."),
    ] = None,
    data_dir: Annotated[Path | None, typer.Option(help="Override the data directory.")] = None,
    debug: Annotated[bool | None, typer.Option(help="Enable debug output.")] = None,
    version: Annotated[
        bool | None,
        typer.Option("--version", callback=version_callback, is_eager=True, help="Show version."),
    ] = None,
) -> None:
    del version
    execution_log_dir = configure_execution_logging(debug=bool(debug), console=console)
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
    provider_commands = {
        "metadata",
        "update",
        "parse",
        "summarize",
        "outline",
        "fix-abstracts",
        "run",
    }
    provider_health = (
        check_provider_health(settings.providers)
        if command in provider_commands
        else ProviderHealthSnapshot({})
    )
    for status in provider_health.statuses.values():
        log = logger.info if status.available else logger.warning
        log(
            "provider health: provider=%s available=%s detail=%s",
            status.name,
            status.available,
            status.detail,
        )
    state = AppState(settings, execution_log_dir, provider_health, LlmCallStats())
    ctx.call_on_close(lambda: finish_execution(command, state))
    ctx.obj = state
