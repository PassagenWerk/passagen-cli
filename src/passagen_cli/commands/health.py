"""External service reachability check command."""

import logging

import typer
from passagen.providers import check_provider_health
from rich.table import Table

from passagen_cli.runtime import ConsoleProgress, console, get_state

logger = logging.getLogger(__name__)


def check_command(ctx: typer.Context) -> None:
    settings = get_state(ctx).settings
    with ConsoleProgress(console, "Checking external services..."):
        snapshot = check_provider_health(settings.providers)

    table = Table()
    table.add_column("Provider", no_wrap=True)
    table.add_column("Status", no_wrap=True)
    table.add_column("Detail")
    for status in sorted(snapshot.statuses.values(), key=lambda item: item.name):
        label = "[green]available[/green]" if status.available else "[red]unavailable[/red]"
        table.add_row(status.name, label, status.detail)
    console.print(table)

    unavailable = [status.name for status in snapshot.statuses.values() if not status.available]
    if unavailable:
        logger.warning("external services unavailable: %s", ", ".join(unavailable))
        raise typer.Exit(code=1)
    logger.info("external service check passed: all providers available")
