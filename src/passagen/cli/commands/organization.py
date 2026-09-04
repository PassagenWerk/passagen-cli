"""Collection and tag management commands."""

import logging
from typing import Annotated, NoReturn

import typer
from rich.table import Table

from passagen.catalog import CatalogError, CatalogService
from passagen.cli.runtime import console, get_state

collection_app = typer.Typer(help="Manage ordered paper collections.")
tag_app = typer.Typer(help="Manage paper tags.")
logger = logging.getLogger(__name__)


def _catalog(ctx: typer.Context) -> CatalogService:
    settings = get_state(ctx).settings
    return CatalogService(settings.resolved_database_path, settings.resolved_data_dir)


def _fail(kind: str, action: str, exc: CatalogError) -> NoReturn:
    logger.error("%s %s failed: %s", kind, action, exc)
    console.print(f"[red]{kind.title()} error:[/red] {exc}", highlight=False)
    raise typer.Exit(code=1) from exc


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
