"""Configuration inspection sub-app."""

import logging

import typer
from passagen.prompting import (
    PromptTemplateError,
    load_outline_prompt_template,
    load_qa_prompt_templates,
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
        load_qa_prompt_templates(
            settings.assistant.rewrite_prompt_path,
            settings.assistant.answer_prompt_path,
            settings.assistant.repair_prompt_path,
        )
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
    default_llm = settings.providers.llm.default
    table.add_row("providers.llm.default.base_url", default_llm.base_url)
    table.add_row("providers.llm.default.model", default_llm.model)
    table.add_row("providers.llm.default.flavor", default_llm.flavor.value)
    table.add_row("providers.llm.default.reasoning", default_llm.reasoning.value)
    table.add_row("providers.llm.default.max_context_window", str(default_llm.max_context_window))
    for name, profile in settings.providers.llm.profiles.items():
        table.add_row(f"providers.llm.profiles.{name}.model", profile.model)
    for purpose, profile_name in settings.providers.llm.tasks.items():
        table.add_row(f"providers.llm.tasks.{purpose.value}", profile_name)
    table.add_row(
        "assistant.rewrite_max_output_tokens",
        str(settings.assistant.rewrite_max_output_tokens),
    )
    table.add_row(
        "assistant.answer_max_output_tokens",
        str(settings.assistant.answer_max_output_tokens),
    )
    table.add_row(
        "assistant.truncated_response_max_attempts",
        str(settings.assistant.truncated_response_max_attempts),
    )
    table.add_row("assistant.max_history_messages", str(settings.assistant.max_history_messages))
    table.add_row("assistant.max_raw_sections", str(settings.assistant.max_raw_sections))
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
