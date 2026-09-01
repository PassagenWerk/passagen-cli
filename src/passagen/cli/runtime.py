"""Shared CLI runtime state, console output, and execution lifecycle."""

from __future__ import annotations

import logging
from pathlib import Path

import typer
from rich.console import Console
from rich.status import Status
from rich.table import Table

from passagen.config import Settings
from passagen.providers import LlmCallStats, LlmStage, ProviderHealthSnapshot

console = Console()
logger = logging.getLogger(__name__)


class AppState:
    def __init__(
        self,
        settings: Settings,
        execution_log_dir: Path,
        provider_health: ProviderHealthSnapshot,
        llm_stats: LlmCallStats,
    ) -> None:
        self.settings = settings
        self.execution_log_dir = execution_log_dir
        self.provider_health = provider_health
        self.llm_stats = llm_stats


class ConsoleProgress:
    def __init__(self, output: Console, initial_message: str) -> None:
        self.output = output
        self.initial_message = initial_message
        self.status: Status | None = None

    def __enter__(self) -> ConsoleProgress:
        if self.output.is_terminal:
            self.status = self.output.status(self.initial_message)
            self.status.start()
        else:
            self.output.print(self.initial_message, markup=False)
        return self

    def update(self, message: str) -> None:
        if self.status is not None:
            self.status.update(message)
        else:
            self.output.print(message, markup=False)

    def __exit__(self, *_args: object) -> None:
        if self.status is not None:
            self.status.stop()


def get_state(ctx: typer.Context) -> AppState:
    state = ctx.find_root().obj
    if not isinstance(state, AppState):
        raise RuntimeError("Application state is not initialized")
    return state


def finish_execution(command: str, state: AppState) -> None:
    usage = state.llm_stats.total
    if usage.calls:
        table = Table(title="LLM usage")
        table.add_column("Stage")
        table.add_column("Calls", justify="right")
        table.add_column("Total tokens", justify="right")
        table.add_column("Input tokens", justify="right")
        table.add_column("Output tokens", justify="right")
        rows = [
            ("total", usage),
            *((stage.value, state.llm_stats.by_stage[stage]) for stage in LlmStage),
        ]
        for stage, stage_usage in rows:
            table.add_row(
                stage,
                str(stage_usage.calls),
                str(stage_usage.total_tokens),
                str(stage_usage.input_tokens),
                str(stage_usage.output_tokens),
            )
            logger.info(
                "llm usage: stage=%s calls=%s total_tokens=%s input_tokens=%s output_tokens=%s",
                stage,
                stage_usage.calls,
                stage_usage.total_tokens,
                stage_usage.input_tokens,
                stage_usage.output_tokens,
            )
        console.print(table)
    logger.info("execution finished: command=%s", command)
