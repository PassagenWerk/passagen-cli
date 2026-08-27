from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from passagen.config import MetadataSettings
from passagen.metadata import (
    ArxivClient,
    BibliographicMetadata,
    CrossrefClient,
    MetadataLookup,
    MetadataLookupError,
    PdfMetadataError,
    extract_pdf_metadata,
    merge_metadata,
)
from passagen.models import PaperStatus
from passagen.repository import (
    MetadataConflictError,
    PaperRecord,
    get_paper,
    update_paper_metadata,
)


class MetadataResolutionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class MetadataResolutionResult:
    paper: PaperRecord
    warnings: tuple[str, ...] = ()
    updated: bool = True


def resolve_paper_metadata(
    database_path: Path,
    data_dir: Path,
    paper_id: str,
    settings: MetadataSettings,
    *,
    refresh: bool = False,
    crossref: MetadataLookup | None = None,
    arxiv: MetadataLookup | None = None,
) -> MetadataResolutionResult:
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise MetadataResolutionError(f"Paper not found: {paper_id}")
    if paper.status not in {PaperStatus.DISCOVERED, PaperStatus.FAILED} and not refresh:
        return MetadataResolutionResult(paper=paper, updated=False)
    if paper.managed_pdf_path is None:
        raise MetadataResolutionError(f"Paper has no managed PDF artifact: {paper_id}")

    pdf_path = data_dir / paper.managed_pdf_path
    if not pdf_path.is_file():
        raise MetadataResolutionError(f"Managed PDF does not exist: {pdf_path}")
    try:
        local = extract_pdf_metadata(
            pdf_path,
            first_pages=settings.first_pages,
            filename_hint=paper.original_filename,
        )
    except PdfMetadataError as exc:
        raise MetadataResolutionError(str(exc)) from exc

    warnings: list[str] = []
    arxiv_metadata = _lookup(
        "arXiv",
        local.arxiv_id,
        arxiv
        or ArxivClient(
            base_url=settings.arxiv.base_url,
            timeout_seconds=settings.timeout_seconds,
        ),
        enabled=settings.arxiv.enabled,
        warnings=warnings,
    )
    crossref_metadata = _lookup(
        "Crossref",
        local.doi,
        crossref
        or CrossrefClient(
            base_url=settings.crossref.base_url,
            timeout_seconds=settings.timeout_seconds,
            mailto=settings.crossref.mailto,
        ),
        enabled=settings.crossref.enabled,
        warnings=warnings,
    )
    if not _titles_match(local.title, crossref_metadata.title):
        warnings.append(
            f"Crossref title does not match PDF title for {local.doi}; ignoring response"
        )
        crossref_metadata = BibliographicMetadata()
    existing = _existing_metadata(paper)
    metadata = merge_metadata(local, arxiv_metadata, crossref_metadata, existing)
    target_status = (
        PaperStatus.METADATA_RESOLVED
        if paper.status in {PaperStatus.DISCOVERED, PaperStatus.FAILED}
        else paper.status
    )
    try:
        updated = update_paper_metadata(database_path, paper_id, metadata, target_status)
    except MetadataConflictError as exc:
        raise MetadataResolutionError(str(exc)) from exc
    return MetadataResolutionResult(paper=updated, warnings=tuple(warnings))


def _lookup(
    provider: str,
    identifier: str | None,
    client: MetadataLookup,
    *,
    enabled: bool,
    warnings: list[str],
) -> BibliographicMetadata:
    if not enabled or identifier is None:
        return BibliographicMetadata()
    try:
        result = client.lookup(identifier)
    except MetadataLookupError as exc:
        warnings.append(str(exc))
        return BibliographicMetadata()
    if result is None:
        warnings.append(f"{provider} did not find metadata for {identifier}")
        return BibliographicMetadata()
    return result


def _existing_metadata(paper: PaperRecord) -> BibliographicMetadata:
    def user_value(name: str, value: object) -> object | None:
        source = paper.metadata_sources.get(name)
        return value if value not in (None, (), "") and source in (None, "user") else None

    title = user_value("title", paper.title)
    authors = user_value("authors", paper.authors)
    year = user_value("year", paper.year)
    venue = user_value("venue", paper.venue)
    doi = user_value("doi", paper.doi)
    arxiv_id = user_value("arxiv_id", paper.arxiv_id)
    source_url = user_value("source_url", paper.source_url)
    values = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "source_url": source_url,
    }
    sources = {name: "user" for name, value in values.items() if value is not None}
    return BibliographicMetadata(
        title=str(title) if title is not None else None,
        authors=tuple(str(author) for author in authors) if isinstance(authors, tuple) else (),
        year=int(year) if isinstance(year, int) else None,
        venue=str(venue) if venue is not None else None,
        doi=str(doi) if doi is not None else None,
        arxiv_id=str(arxiv_id) if arxiv_id is not None else None,
        source_url=str(source_url) if source_url is not None else None,
        sources=sources,
    )


def _titles_match(expected: str | None, actual: str | None) -> bool:
    if expected is None or actual is None:
        return True
    expected_words = set(re.findall(r"[a-z0-9]+", expected.lower()))
    actual_words = set(re.findall(r"[a-z0-9]+", actual.lower()))
    if not expected_words or not actual_words:
        return True
    return len(expected_words & actual_words) / min(len(expected_words), len(actual_words)) >= 0.6
