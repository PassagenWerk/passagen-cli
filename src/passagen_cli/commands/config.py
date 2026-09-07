"""Configuration inspection sub-app."""

import logging

import typer
from passagen.prompting import (
    PromptTemplateError,
    load_outline_prompt_template,
    load_summary_prompt_templates,
)
from rich.table import Table

from passagen_cli.runtime import console, get_state

config_app = typer.Typer(help="Inspect Passagen configuration and prompt templates.")
logger = logging.getLogger(__name__)


@config_app.command(
    "check",
    help="Validate configuration and prompt templates, then show effective settings.",
)
def config_check(ctx: typer.Context) -> None:
    settings = get_state(ctx).settings
    logger.info("config check started")
    try:
        load_summary_prompt_templates(
            settings.pipeline.summarization.facts_prompt_path,
            settings.pipeline.summarization.summary_prompt_path,
            settings.pipeline.summarization.repair_prompt_path,
            settings.pipeline.summarization.full_prompt_path,
            settings.pipeline.summarization.reduce_prompt_path,
        )
        load_outline_prompt_template(settings.pipeline.outlining.prompt_path)
    except PromptTemplateError as exc:
        logger.error("prompt configuration failed: %s", exc)
        console.print(f"[red]Prompt configuration error:[/red] {exc}", highlight=False)
        raise typer.Exit(code=2) from exc
    table = Table(show_header=False)
    table.add_row("data_dir", str(settings.resolved_data_dir))
    table.add_row("database_path", str(settings.resolved_database_path))
    table.add_row("debug", str(settings.debug).lower())
    table.add_row("providers.crossref", str(settings.providers.crossref.enabled).lower())
    table.add_row("providers.arxiv", str(settings.providers.arxiv.enabled).lower())
    table.add_row("providers.citation_page", str(settings.providers.citation_page.enabled).lower())
    table.add_row("providers.openalex", str(settings.providers.openalex.enabled).lower())
    table.add_row("providers.grobid.base_url", settings.providers.grobid.base_url)
    table.add_row("providers.llm.base_url", settings.providers.llm.base_url)
    table.add_row("providers.llm.model", settings.providers.llm.model)
    table.add_row(
        "providers.llm.disable_thinking", str(settings.providers.llm.disable_thinking).lower()
    )
    table.add_row(
        "providers.llm.context_window_tokens", str(settings.providers.llm.context_window_tokens)
    )
    table.add_row(
        "providers.llm.max_context_utilization",
        str(settings.providers.llm.max_context_utilization),
    )
    table.add_row(
        "providers.llm.safety_margin_tokens", str(settings.providers.llm.safety_margin_tokens)
    )
    table.add_row("providers.llm.chars_per_token", str(settings.providers.llm.chars_per_token))
    table.add_row("pipeline.metadata.first_pages", str(settings.pipeline.metadata.first_pages))
    table.add_row("pipeline.parsing.parser", settings.pipeline.parsing.parser.value)
    table.add_row("pipeline.summarization.strategy", settings.pipeline.summarization.strategy.value)
    table.add_row(
        "pipeline.summarization.chunk_max_input_tokens",
        str(settings.pipeline.summarization.chunk_max_input_tokens),
    )
    table.add_row(
        "pipeline.summarization.chunk_overlap_paragraphs",
        str(settings.pipeline.summarization.chunk_overlap_paragraphs),
    )
    table.add_row(
        "pipeline.outlining.max_output_tokens",
        str(settings.pipeline.outlining.max_output_tokens),
    )
    table.add_row(
        "pipeline.summarization.facts_prompt_path",
        str(settings.pipeline.summarization.facts_prompt_path or "built-in"),
    )
    table.add_row(
        "pipeline.summarization.summary_prompt_path",
        str(settings.pipeline.summarization.summary_prompt_path or "built-in"),
    )
    table.add_row(
        "pipeline.summarization.full_prompt_path",
        str(settings.pipeline.summarization.full_prompt_path or "built-in"),
    )
    table.add_row(
        "pipeline.summarization.reduce_prompt_path",
        str(settings.pipeline.summarization.reduce_prompt_path or "built-in"),
    )
    table.add_row(
        "pipeline.summarization.repair_prompt_path",
        str(settings.pipeline.summarization.repair_prompt_path or "built-in"),
    )
    table.add_row(
        "pipeline.outlining.prompt_path",
        str(settings.pipeline.outlining.prompt_path or "built-in"),
    )
    console.print(table)
