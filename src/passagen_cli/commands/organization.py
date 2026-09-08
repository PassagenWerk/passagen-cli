"""Collection and tag management commands."""

import logging
from enum import StrEnum
from pathlib import Path
from typing import Annotated, NoReturn

import typer
from passagen.assistant import (
    AssistantError,
    AssistantTurn,
    ConversationScope,
    ConversationService,
    ScopeError,
    SourceStatus,
)
from passagen.catalog import CatalogError, CatalogService
from passagen.providers import LlmProviderError
from passagen.research import (
    CollectionReportResult,
    CollectionReportService,
    CollectionSynthesisResult,
    CollectionSynthesisService,
    ReportKind,
    render_report_json,
    render_report_markdown,
    render_synthesis_json,
    render_synthesis_markdown,
)
from rich.table import Table

from passagen_cli.runtime import console, get_state

collection_app = typer.Typer(help="Manage ordered paper collections.")
tag_app = typer.Typer(help="Manage paper tags.")
logger = logging.getLogger(__name__)


class SynthesisFormat(StrEnum):
    MARKDOWN = "markdown"
    JSON = "json"


def _catalog(ctx: typer.Context) -> CatalogService:
    settings = get_state(ctx).settings
    return CatalogService(settings.resolved_database_path, settings.resolved_data_dir)


def _synthesis_service(ctx: typer.Context) -> CollectionSynthesisService:
    settings = get_state(ctx).settings
    return CollectionSynthesisService(
        settings.resolved_database_path,
        settings.resolved_data_dir,
        settings.providers.llm,
        settings.assistant,
    )


def _conversation_service(ctx: typer.Context) -> ConversationService:
    settings = get_state(ctx).settings
    # ConversationService writes without a schema check; the CatalogService
    # constructor raises IncompatibleSchemaError for an uninitialized database.
    CatalogService(settings.resolved_database_path, settings.resolved_data_dir)
    return ConversationService(
        settings.resolved_database_path,
        settings.resolved_data_dir,
        settings.providers.llm,
        settings.assistant,
    )


def _report_service(ctx: typer.Context) -> CollectionReportService:
    settings = get_state(ctx).settings
    return CollectionReportService(
        settings.resolved_database_path,
        settings.resolved_data_dir,
        settings.providers.llm,
        settings.assistant,
    )


def _fail(kind: str, action: str, exc: CatalogError) -> NoReturn:
    logger.error("%s %s failed: %s", kind, action, exc)
    console.print(f"[red]{kind.title()} error:[/red] {exc}", highlight=False)
    raise typer.Exit(code=1) from exc


def _run_synthesis(
    ctx: typer.Context,
    collection_id: str,
    *,
    output_format: SynthesisFormat,
    allow_partial: bool,
    force: bool,
    comparison_only: bool,
) -> None:
    try:
        result = _synthesis_service(ctx).synthesize(
            collection_id, allow_partial=allow_partial, force=force
        )
    except (AssistantError, CatalogError, LlmProviderError) as exc:
        logger.info("collection synthesis failed: collection_id=%s error=%s", collection_id, exc)
        typer.echo(f"Collection synthesis error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_format is SynthesisFormat.JSON:
        if comparison_only:
            payload = result.synthesis.comparison_matrix.model_dump_json(indent=2) + "\n"
        else:
            payload = render_synthesis_json(result.synthesis).decode("utf-8")
    else:
        payload = render_synthesis_markdown(result.synthesis)
    typer.echo(payload, nl=False)
    _report_synthesis(result, comparison_only=comparison_only)


def _report_synthesis(result: CollectionSynthesisResult, *, comparison_only: bool) -> None:
    operation = "comparison" if comparison_only else "synthesis"
    detail = f"{result.disposition}, strategy={result.strategy}"
    if result.run_id is not None:
        detail += f", run={result.run_id}"
    typer.echo(f"Collection {operation}: {detail}", err=True)
    missing = result.synthesis.coverage.missing_summary_paper_ids
    if missing:
        typer.echo(f"Partial coverage; missing summaries: {', '.join(missing)}", err=True)


def _report_source_status(status: SourceStatus) -> None:
    if status.stale:
        typer.echo(f"Stale sources: {'; '.join(status.reasons)}", err=True)


def _run_report(
    ctx: typer.Context,
    collection_id: str,
    *,
    kind: ReportKind,
    user_prompt: str | None,
    output_format: SynthesisFormat,
    allow_partial: bool,
    force: bool,
) -> None:
    try:
        result = _report_service(ctx).create_report(
            collection_id,
            kind,
            user_prompt=user_prompt,
            allow_partial=allow_partial,
            force=force,
        )
    except (AssistantError, CatalogError, LlmProviderError) as exc:
        logger.info("collection report failed: collection_id=%s error=%s", collection_id, exc)
        typer.echo(f"Collection report error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    if output_format is SynthesisFormat.JSON:
        payload = render_report_json(result.report).decode("utf-8")
    else:
        payload = render_report_markdown(result.report)
    typer.echo(payload, nl=False)
    _report_report_result(result)


def _report_report_result(result: CollectionReportResult) -> None:
    detail = result.disposition
    if result.record.run_id is not None:
        detail += f", run={result.record.run_id}"
    typer.echo(f"Collection {result.record.kind.value} report: {detail}", err=True)
    missing = result.report.coverage.missing_summary_paper_ids
    if missing:
        typer.echo(f"Partial coverage; missing summaries: {', '.join(missing)}", err=True)
    _report_source_status(result.source_status)


@collection_app.command("create", help="Create an empty collection.")
def create_collection(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Collection name.")],
) -> None:
    try:
        collection = _catalog(ctx).create_collection(name)
    except CatalogError as exc:
        _fail("collection", "create", exc)
    console.print(f"Collection created: {collection.id} {collection.name}", markup=False)


@collection_app.command("list", help="List collections and their IDs.")
def list_collections(ctx: typer.Context) -> None:
    try:
        catalog = _catalog(ctx)
        collections = [catalog.get_collection(item.id) for item in catalog.list_collections()]
    except CatalogError as exc:
        _fail("collection", "list", exc)
    table = Table()
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Papers", justify="right")
    table.add_column("Description")
    for collection in collections:
        table.add_row(
            collection.id,
            collection.name,
            str(len(collection.papers)),
            collection.description or "-",
        )
    console.print(table)


@collection_app.command("delete", help="Delete a collection without deleting its papers.")
def delete_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
) -> None:
    try:
        catalog = _catalog(ctx)
        collection = catalog.get_collection(collection_id)
        catalog.delete_collection(collection_id)
    except CatalogError as exc:
        _fail("collection", "delete", exc)
    console.print(f"Collection deleted: {collection.name}", markup=False)


@collection_app.command("add", help="Append a paper to a collection.")
def add_collection_paper(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
) -> None:
    try:
        _catalog(ctx).add_collection_papers(collection_id, [paper_id])
    except CatalogError as exc:
        _fail("collection", "add", exc)
    console.print(f"Paper added to collection: {collection_id} <- {paper_id}", markup=False)


@collection_app.command("remove", help="Remove a paper from a collection.")
def remove_collection_paper(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
) -> None:
    try:
        _catalog(ctx).remove_collection_paper(collection_id, paper_id)
    except CatalogError as exc:
        _fail("collection", "remove", exc)
    console.print(f"Paper removed from collection: {collection_id} <- {paper_id}", markup=False)


@collection_app.command("rename", help="Rename a collection while preserving its description.")
def rename_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    new_name: Annotated[str, typer.Argument(help="New collection name.")],
) -> None:
    try:
        catalog = _catalog(ctx)
        current = catalog.get_collection(collection_id)
        collection = catalog.update_collection(
            collection_id, name=new_name, description=current.description
        )
    except CatalogError as exc:
        _fail("collection", "rename", exc)
    console.print(f"Collection renamed: {collection.name}", markup=False)


@collection_app.command("synthesize", help="Generate or reuse a collection synthesis.")
def synthesize_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    output_format: Annotated[
        SynthesisFormat, typer.Option("--format", help="Output format: markdown or json.")
    ] = SynthesisFormat.MARKDOWN,
    allow_partial: Annotated[
        bool, typer.Option(help="Proceed when some papers lack valid summaries.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Generate a new synthesis instead of reusing one.")
    ] = False,
) -> None:
    _run_synthesis(
        ctx,
        collection_id,
        output_format=output_format,
        allow_partial=allow_partial,
        force=force,
        comparison_only=False,
    )


@collection_app.command("compare", help="Generate or reuse a collection paper comparison.")
def compare_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    output_format: Annotated[
        SynthesisFormat, typer.Option("--format", help="Output format: markdown or json.")
    ] = SynthesisFormat.MARKDOWN,
    allow_partial: Annotated[
        bool, typer.Option(help="Proceed when some papers lack valid summaries.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Generate a new synthesis instead of reusing one.")
    ] = False,
) -> None:
    _run_synthesis(
        ctx,
        collection_id,
        output_format=output_format,
        allow_partial=allow_partial,
        force=force,
        comparison_only=True,
    )


@collection_app.command("ask", help="Ask a question against the whole collection.")
def ask_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    question: Annotated[str, typer.Argument(help="Question to answer from collection sources.")],
    conversation_id: Annotated[
        str | None,
        typer.Option(help="Continue an existing collection conversation instead of creating one."),
    ] = None,
    output_format: Annotated[
        SynthesisFormat, typer.Option("--format", help="Output format: markdown or json.")
    ] = SynthesisFormat.MARKDOWN,
    force: Annotated[
        bool, typer.Option(help="Regenerate the answer instead of reusing a matching one.")
    ] = False,
) -> None:
    created_conversation = False
    try:
        service = _conversation_service(ctx)
        if conversation_id is None:
            conversation = service.create_conversation(collection_id=collection_id)
            created_conversation = True
        else:
            conversation = service.get_conversation(conversation_id).conversation
            if (
                conversation.scope is not ConversationScope.COLLECTION
                or conversation.collection_id != collection_id
            ):
                raise ScopeError(
                    f"Conversation {conversation_id} does not belong to collection {collection_id}"
                )
        turn = service.ask(conversation.id, question, force_regenerate=force)
    except (AssistantError, CatalogError, LlmProviderError) as exc:
        logger.info("collection ask failed: collection_id=%s error=%s", collection_id, exc)
        typer.echo(f"Collection ask error: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    answer = turn.qa_record.answer
    if output_format is SynthesisFormat.JSON:
        payload = answer.model_dump_json(indent=2) + "\n"
    else:
        payload = answer.answer_markdown.rstrip() + "\n"
    typer.echo(payload, nl=False)
    _report_turn(turn, created_conversation=created_conversation, service=service)


def _report_turn(
    turn: AssistantTurn, *, created_conversation: bool, service: ConversationService
) -> None:
    if created_conversation:
        typer.echo(f"Collection conversation created: {turn.conversation_id}", err=True)
    typer.echo(
        f"Collection answer: {turn.disposition.value}, run={turn.run_id}, "
        f"conversation={turn.conversation_id}",
        err=True,
    )
    _report_source_status(service.source_status(turn.qa_record))


@collection_app.command("review", help="Generate or reuse a collection review report.")
def review_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    output_format: Annotated[
        SynthesisFormat, typer.Option("--format", help="Output format: markdown or json.")
    ] = SynthesisFormat.MARKDOWN,
    allow_partial: Annotated[
        bool, typer.Option(help="Proceed when some papers lack valid summaries.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Generate a new report instead of reusing one.")
    ] = False,
) -> None:
    _run_report(
        ctx,
        collection_id,
        kind=ReportKind.REVIEW,
        user_prompt=None,
        output_format=output_format,
        allow_partial=allow_partial,
        force=force,
    )


@collection_app.command("research", help="Generate or reuse a collection research report.")
def research_collection(
    ctx: typer.Context,
    collection_id: Annotated[str, typer.Argument(help="Collection ID.")],
    kind: Annotated[
        ReportKind, typer.Option(help="Report kind: review, comparison, gaps, or custom.")
    ] = ReportKind.CUSTOM,
    prompt: Annotated[
        str | None, typer.Option(help="Custom research question (custom kind only).")
    ] = None,
    prompt_file: Annotated[
        Path | None,
        typer.Option(
            help="Read the custom research question from a UTF-8 file (custom kind only).",
            exists=True,
            file_okay=True,
            dir_okay=False,
            readable=True,
        ),
    ] = None,
    output_format: Annotated[
        SynthesisFormat, typer.Option("--format", help="Output format: markdown or json.")
    ] = SynthesisFormat.MARKDOWN,
    allow_partial: Annotated[
        bool, typer.Option(help="Proceed when some papers lack valid summaries.")
    ] = False,
    force: Annotated[
        bool, typer.Option(help="Generate a new report instead of reusing one.")
    ] = False,
) -> None:
    user_prompt = _resolve_custom_prompt(kind, prompt, prompt_file)
    _run_report(
        ctx,
        collection_id,
        kind=kind,
        user_prompt=user_prompt,
        output_format=output_format,
        allow_partial=allow_partial,
        force=force,
    )


def _resolve_custom_prompt(
    kind: ReportKind, prompt: str | None, prompt_file: Path | None
) -> str | None:
    if prompt is not None and prompt_file is not None:
        typer.echo("--prompt and --prompt-file are mutually exclusive", err=True)
        raise typer.Exit(code=2)
    if prompt_file is not None:
        try:
            prompt = prompt_file.read_text(encoding="utf-8")
        except OSError as exc:
            typer.echo(f"Cannot read prompt file {prompt_file}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    if kind is ReportKind.CUSTOM:
        if prompt is None or not prompt.strip():
            typer.echo(
                "Custom research reports require a question via --prompt or --prompt-file",
                err=True,
            )
            raise typer.Exit(code=2)
    elif prompt is not None:
        typer.echo("--prompt/--prompt-file are only valid with --kind custom", err=True)
        raise typer.Exit(code=2)
    return prompt


@tag_app.command("create", help="Create a tag without a color.")
def create_tag(
    ctx: typer.Context,
    name: Annotated[str, typer.Argument(help="Tag name.")],
) -> None:
    try:
        tag = _catalog(ctx).create_tag(name)
    except CatalogError as exc:
        _fail("tag", "create", exc)
    console.print(f"Tag created: {tag.id} {tag.name}", markup=False)


@tag_app.command("list", help="List tags and their IDs.")
def list_tags(ctx: typer.Context) -> None:
    try:
        tags = _catalog(ctx).list_tags()
    except CatalogError as exc:
        _fail("tag", "list", exc)
    table = Table()
    table.add_column("ID", no_wrap=True)
    table.add_column("Name")
    table.add_column("Color")
    for tag in tags:
        table.add_row(tag.id, tag.name, tag.color or "-")
    console.print(table)


@tag_app.command("delete", help="Delete a tag and remove it from all papers.")
def delete_tag(
    ctx: typer.Context,
    tag_id: Annotated[str, typer.Argument(help="Tag ID.")],
) -> None:
    try:
        catalog = _catalog(ctx)
        tag = catalog.get_tag(tag_id)
        catalog.delete_tag(tag_id)
    except CatalogError as exc:
        _fail("tag", "delete", exc)
    console.print(f"Tag deleted: {tag.name}", markup=False)


@tag_app.command("add", help="Add a tag to a paper.")
def add_paper_tag(
    ctx: typer.Context,
    tag_id: Annotated[str, typer.Argument(help="Tag ID.")],
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
) -> None:
    try:
        _catalog(ctx).add_paper_tag(paper_id, tag_id)
    except CatalogError as exc:
        _fail("tag", "add", exc)
    console.print(f"Tag added to paper: {tag_id} -> {paper_id}", markup=False)


@tag_app.command("remove", help="Remove a tag from a paper.")
def remove_paper_tag(
    ctx: typer.Context,
    tag_id: Annotated[str, typer.Argument(help="Tag ID.")],
    paper_id: Annotated[str, typer.Argument(help="Paper ID.")],
) -> None:
    try:
        _catalog(ctx).remove_paper_tag(paper_id, tag_id)
    except CatalogError as exc:
        _fail("tag", "remove", exc)
    console.print(f"Tag removed from paper: {tag_id} -> {paper_id}", markup=False)


@tag_app.command("rename", help="Rename a tag while preserving its color.")
def rename_tag(
    ctx: typer.Context,
    tag_id: Annotated[str, typer.Argument(help="Tag ID.")],
    new_name: Annotated[str, typer.Argument(help="New tag name.")],
) -> None:
    try:
        catalog = _catalog(ctx)
        current = catalog.get_tag(tag_id)
        tag = catalog.update_tag(tag_id, name=new_name, color=current.color)
    except CatalogError as exc:
        _fail("tag", "rename", exc)
    console.print(f"Tag renamed: {tag.name}", markup=False)
