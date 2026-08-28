from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

import httpx
import pymupdf

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:A-Z0-9]+", re.IGNORECASE)
_DOI_LINE_BREAK_PATTERN = re.compile(
    r"(?P<prefix>10\.\d{4,9}/[-._;()/:A-Z0-9]+\.)[ \t]*\r?\n[ \t]*"
    r"(?P<suffix>\d{4,}\b)",
    re.IGNORECASE,
)
_ARXIV_PATTERN = re.compile(
    r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)"
    r"(?P<id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)",
    re.IGNORECASE,
)
_BARE_ARXIV_PATTERN = re.compile(
    r"^(?P<id>(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?)$",
    re.IGNORECASE,
)
_ARXIV_VERSION = re.compile(r"v\d+$", re.IGNORECASE)
_URL_PATTERN = re.compile(r"https?://[^\s<>()]+", re.IGNORECASE)
_AFFILIATION_WORDS = {
    "academy",
    "alibaba",
    "amazon",
    "cloud",
    "college",
    "company",
    "corporation",
    "department",
    "google",
    "ibm",
    "inc",
    "institute",
    "laboratory",
    "labs",
    "meta",
    "microsoft",
    "research",
    "school",
    "university",
}
_ATOM = "http://www.w3.org/2005/Atom"
_ARXIV = "http://arxiv.org/schemas/atom"


class PdfMetadataError(RuntimeError):
    pass


class MetadataLookupError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class BibliographicMetadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source_url: str | None = None
    sources: dict[str, str] = field(default_factory=dict)


class MetadataLookup(Protocol):
    def lookup(self, identifier: str) -> BibliographicMetadata | None: ...


class PdfMetadataLookup(Protocol):
    def extract(self, path: Path) -> BibliographicMetadata | None: ...


@dataclass(frozen=True, slots=True)
class _TextSpan:
    page: int
    text: str
    size: float
    x0: float
    y0: float
    y1: float


@dataclass(frozen=True, slots=True)
class _TitleCandidate:
    text: str
    page: int
    size: float
    y1: float
    score: float


def extract_pdf_metadata(
    path: Path,
    *,
    first_pages: int,
    filename_hint: str | None = None,
) -> BibliographicMetadata:
    try:
        with pymupdf.open(path) as document:
            if document.needs_pass:
                raise PdfMetadataError(f"PDF is encrypted: {path}")
            raw_metadata = document.metadata or {}
            spans = _extract_spans(document, first_pages)
            text = "\n".join(
                str(document[index].get_text("text"))
                for index in range(min(first_pages, document.page_count))
            )
    except PdfMetadataError:
        raise
    except (OSError, RuntimeError, ValueError) as exc:
        raise PdfMetadataError(f"Cannot read PDF metadata from {path}: {exc}") from exc

    metadata_text = "\n".join(str(value) for value in raw_metadata.values() if value)
    searchable_text = f"{metadata_text}\n{text}"
    filename_title = _title_from_filename(filename_hint)
    title_candidate = _layout_title(spans, filename_title)
    embedded_title = _clean_text(raw_metadata.get("title"))
    title = (
        embedded_title
        if _is_usable_embedded_title(embedded_title)
        else title_candidate.text
        if title_candidate is not None
        else filename_title or _first_text_line(text)
    )
    authors = _parse_authors(raw_metadata.get("author"))
    if not authors and title_candidate is not None:
        authors = _layout_authors(spans, title_candidate)
    year = _extract_year(raw_metadata.get("creationDate"))
    doi = extract_doi(searchable_text)
    arxiv_id = extract_arxiv_id(searchable_text) or _extract_bare_arxiv_id(path.stem)
    venue = _extract_venue(text)
    source_url = _extract_source_url(text)
    values: dict[str, object] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "source_url": source_url,
    }
    sources = {name: "pdf" for name, value in values.items() if value}
    return BibliographicMetadata(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        source_url=source_url,
        sources=sources,
    )


def extract_doi(text: str) -> str | None:
    joined_text = _DOI_LINE_BREAK_PATTERN.sub(r"\g<prefix>\g<suffix>", text)
    candidates = [normalize_doi(match.group(0)) for match in _DOI_PATTERN.finditer(joined_text)]
    if not candidates:
        return None
    counts = Counter(candidates)
    return max(counts, key=lambda candidate: (counts[candidate], len(candidate)))


def normalize_doi(value: str) -> str:
    normalized = value.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.startswith(prefix):
            normalized = normalized.removeprefix(prefix).strip()
    return normalized.rstrip(".,;:)]}")


def extract_arxiv_id(text: str) -> str | None:
    match = _ARXIV_PATTERN.search(text)
    return normalize_arxiv_id(match.group("id")) if match is not None else None


def normalize_arxiv_id(value: str) -> str:
    normalized = value.strip()
    if normalized.lower().startswith("arxiv:"):
        normalized = normalized.split(":", maxsplit=1)[1].strip()
    return _ARXIV_VERSION.sub("", normalized)


def _extract_bare_arxiv_id(value: str) -> str | None:
    match = _BARE_ARXIV_PATTERN.fullmatch(value.strip())
    return normalize_arxiv_id(match.group("id")) if match is not None else None


def _extract_spans(document: pymupdf.Document, first_pages: int) -> list[_TextSpan]:
    spans: list[_TextSpan] = []
    for page_index in range(min(first_pages, document.page_count)):
        page_dict: Any = document[page_index].get_text("dict")
        blocks = page_dict.get("blocks", []) if isinstance(page_dict, dict) else []
        for block in blocks:
            if not isinstance(block, dict):
                continue
            for line in block.get("lines", []):
                if not isinstance(line, dict):
                    continue
                for raw_span in line.get("spans", []):
                    if not isinstance(raw_span, dict):
                        continue
                    text = _clean_text(raw_span.get("text"))
                    bbox = raw_span.get("bbox")
                    size = raw_span.get("size")
                    if (
                        text is None
                        or not isinstance(bbox, (list, tuple))
                        or len(bbox) != 4
                        or not isinstance(size, (int, float))
                    ):
                        continue
                    spans.append(
                        _TextSpan(
                            page=page_index,
                            text=text,
                            size=float(size),
                            x0=float(bbox[0]),
                            y0=float(bbox[1]),
                            y1=float(bbox[3]),
                        )
                    )
    return spans


def _layout_title(spans: list[_TextSpan], filename_title: str | None) -> _TitleCandidate | None:
    candidates: list[_TitleCandidate] = []
    for page in sorted({span.page for span in spans}):
        page_spans = [span for span in spans if span.page == page and len(span.text) > 1]
        font_sizes = sorted({round(span.size, 1) for span in page_spans}, reverse=True)[:3]
        for font_size in font_sizes:
            same_size = [span for span in page_spans if abs(span.size - font_size) <= 0.3]
            for group in _contiguous_span_groups(same_size):
                text = _clean_text(" ".join(span.text for span in group))
                if text is None or len(_words(text)) < 3 or _URL_PATTERN.search(text):
                    continue
                overlap = _word_overlap(text, filename_title) if filename_title else 0.0
                score = font_size + overlap * 100 + min(len(_words(text)), 10)
                candidates.append(
                    _TitleCandidate(
                        text=text,
                        page=page,
                        size=font_size,
                        y1=max(span.y1 for span in group),
                        score=score,
                    )
                )
    return max(candidates, key=lambda candidate: candidate.score, default=None)


def _contiguous_span_groups(spans: list[_TextSpan]) -> list[list[_TextSpan]]:
    groups: list[list[_TextSpan]] = []
    for span in sorted(spans, key=lambda item: (item.y0, item.x0)):
        if not groups:
            groups.append([span])
            continue
        previous_bottom = max(item.y1 for item in groups[-1])
        if span.y0 - previous_bottom <= max(span.size * 1.4, 8):
            groups[-1].append(span)
        else:
            groups.append([span])
    return groups


def _layout_authors(spans: list[_TextSpan], title: _TitleCandidate) -> tuple[str, ...]:
    author_spans = [
        span
        for span in spans
        if span.page == title.page
        and title.y1 - 1 <= span.y0 <= title.y1 + 90
        and max(8, title.size * 0.5) <= span.size < title.size - 1
        and not _URL_PATTERN.search(span.text)
    ]
    text = " ".join(span.text for span in sorted(author_spans, key=lambda item: (item.y0, item.x0)))
    return _parse_layout_authors(text)


def _parse_layout_authors(text: str) -> tuple[str, ...]:
    normalized = re.sub(r"\s+and\s+", ", ", text, flags=re.IGNORECASE)
    authors: list[str] = []
    for part in normalized.split(","):
        candidate = re.sub(r"^[\d*†‡§\s]+|[\d*†‡§\s]+$", "", part).strip()
        words = candidate.split()
        lowered = {re.sub(r"[^a-z]", "", word.lower()) for word in words}
        if (
            not 2 <= len(words) <= 5
            or lowered & _AFFILIATION_WORDS
            or not all(_looks_like_name_word(word) for word in words)
        ):
            continue
        authors.append(candidate)
    return tuple(dict.fromkeys(authors))


def _looks_like_name_word(word: str) -> bool:
    cleaned = word.strip(".-'’")
    return (
        bool(cleaned) and cleaned[0].isupper() and any(character.isalpha() for character in cleaned)
    )


def _title_from_filename(filename: str | None) -> str | None:
    if filename is None:
        return None
    title = Path(filename).stem.replace("_", " ")
    if " - " in title:
        prefix, candidate = title.split(" - ", maxsplit=1)
        if "et al" in prefix.lower() or len(prefix.split()) <= 6:
            title = candidate
    return _clean_text(title)


def _is_usable_embedded_title(title: str | None) -> bool:
    if title is None or len(_words(title)) < 2:
        return False
    lowered = title.lower()
    rejected = ("untitled", "microsoft word", "this paper is included in the")
    return not any(value in lowered for value in rejected)


def _word_overlap(left: str, right: str | None) -> float:
    if right is None:
        return 0.0
    left_words = _words(left)
    right_words = _words(right)
    all_words = left_words | right_words
    return len(left_words & right_words) / len(all_words) if all_words else 0.0


def _words(value: str) -> set[str]:
    return {word.lower() for word in re.findall(r"[A-Za-z0-9]+", value) if len(word) > 1}


def _extract_venue(text: str) -> str | None:
    normalized = " ".join(text.split())
    match = re.search(
        r"Proceedings of (?:the )?(?P<venue>.+?)(?:\.\s|\b(?:January|February|March|"
        r"April|May|June|July|August|September|October|November|December)\b)",
        normalized,
        re.IGNORECASE,
    )
    return _clean_text(match.group("venue")) if match is not None else None


def _extract_source_url(text: str) -> str | None:
    for match in _URL_PATTERN.finditer(text):
        url = match.group(0).rstrip(".,;:)]}")
        lowered = url.lower()
        if "doi.org/" not in lowered and "arxiv.org/" not in lowered:
            return url
    return None


class CrossrefClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        mailto: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.mailto = mailto
        self.client = client

    def lookup(self, identifier: str) -> BibliographicMetadata | None:
        parameters = {"mailto": self.mailto} if self.mailto else None
        try:
            response = self._get(
                f"{self.base_url}/works/{quote(identifier, safe='')}",
                params=parameters,
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            document: Any = response.json()
            message = document.get("message") if isinstance(document, dict) else None
            if not isinstance(message, dict):
                raise MetadataLookupError("Crossref response does not contain a message object")
            return _crossref_metadata(message, identifier)
        except MetadataLookupError:
            raise
        except (httpx.HTTPError, ValueError) as exc:
            raise MetadataLookupError(f"Crossref lookup failed for {identifier}: {exc}") from exc

    def _get(self, url: str, *, params: dict[str, str] | None) -> httpx.Response:
        if self.client is not None:
            return self.client.get(url, params=params)
        with httpx.Client(timeout=self.timeout_seconds, headers=_http_headers()) as client:
            return client.get(url, params=params)


class ArxivClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client

    def lookup(self, identifier: str) -> BibliographicMetadata | None:
        try:
            response = self._get(
                f"{self.base_url}/api/query",
                params={"id_list": identifier, "max_results": "1"},
            )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (httpx.HTTPError, ET.ParseError) as exc:
            raise MetadataLookupError(f"arXiv lookup failed for {identifier}: {exc}") from exc

        entry = root.find(f"{{{_ATOM}}}entry")
        if entry is None:
            return None
        return _arxiv_metadata(entry, identifier)

    def _get(self, url: str, *, params: dict[str, str]) -> httpx.Response:
        if self.client is not None:
            return self.client.get(url, params=params)
        with httpx.Client(timeout=self.timeout_seconds, headers=_http_headers()) as client:
            return client.get(url, params=params)


class GrobidClient:
    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float,
        client: httpx.Client | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.client = client

    def is_available(self) -> bool:
        try:
            response = self._get(f"{self.base_url}/api/isalive")
            return response.is_success and response.text.strip().lower() == "true"
        except httpx.HTTPError:
            return False

    def extract(self, path: Path) -> BibliographicMetadata | None:
        try:
            with path.open("rb") as pdf_file:
                response = self._post(
                    f"{self.base_url}/api/processHeaderDocument",
                    files={"input": (path.name, pdf_file, "application/pdf")},
                    data={"consolidateHeader": "0"},
                )
            if response.status_code == 204:
                return None
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except (OSError, httpx.HTTPError, ET.ParseError) as exc:
            raise MetadataLookupError(
                f"GROBID header extraction failed for {path.name}: {exc}"
            ) from exc
        return _grobid_metadata(root)

    def _post(
        self,
        url: str,
        *,
        files: dict[str, tuple[str, Any, str]],
        data: dict[str, str],
    ) -> httpx.Response:
        if self.client is not None:
            return self.client.post(url, files=files, data=data)
        with httpx.Client(timeout=self.timeout_seconds, headers=_http_headers()) as client:
            return client.post(url, files=files, data=data)

    def _get(self, url: str) -> httpx.Response:
        if self.client is not None:
            return self.client.get(url)
        with httpx.Client(timeout=self.timeout_seconds, headers=_http_headers()) as client:
            return client.get(url)


def merge_metadata(*items: BibliographicMetadata) -> BibliographicMetadata:
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source_url: str | None = None
    sources: dict[str, str] = {}
    for item in items:
        for name in ("title", "authors", "year", "venue", "doi", "arxiv_id", "source_url"):
            if getattr(item, name) not in (None, (), "") and (source := item.sources.get(name)):
                sources[name] = source
        title = item.title or title
        authors = item.authors or authors
        year = item.year or year
        venue = item.venue or venue
        doi = item.doi or doi
        arxiv_id = item.arxiv_id or arxiv_id
        source_url = item.source_url or source_url
    return BibliographicMetadata(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        source_url=source_url,
        sources=sources,
    )


def _crossref_metadata(message: dict[str, Any], identifier: str) -> BibliographicMetadata:
    title = _first_string(message.get("title"))
    venue = _first_string(message.get("container-title"))
    authors = _crossref_authors(message.get("author"))
    year = _crossref_year(message)
    doi = normalize_doi(str(message.get("DOI") or identifier))
    source_url = _clean_text(message.get("URL"))
    values: dict[str, object] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "source_url": source_url,
    }
    return BibliographicMetadata(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        source_url=source_url,
        sources={name: "crossref" for name, value in values.items() if value},
    )


def _arxiv_metadata(entry: ET.Element, identifier: str) -> BibliographicMetadata:
    title = _element_text(entry, f"{{{_ATOM}}}title")
    authors = tuple(
        name
        for author in entry.findall(f"{{{_ATOM}}}author")
        if (name := _element_text(author, f"{{{_ATOM}}}name")) is not None
    )
    published = _element_text(entry, f"{{{_ATOM}}}published")
    year = int(published[:4]) if published and published[:4].isdigit() else None
    venue = _element_text(entry, f"{{{_ARXIV}}}journal_ref")
    doi_text = _element_text(entry, f"{{{_ARXIV}}}doi")
    doi = normalize_doi(doi_text) if doi_text else None
    source_url = _element_text(entry, f"{{{_ATOM}}}id")
    arxiv_id = normalize_arxiv_id(identifier)
    values: dict[str, object] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "source_url": source_url,
    }
    return BibliographicMetadata(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        source_url=source_url,
        sources={name: "arxiv" for name, value in values.items() if value},
    )


def _grobid_metadata(root: ET.Element) -> BibliographicMetadata:
    namespace = {"tei": "http://www.tei-c.org/ns/1.0"}
    file_desc = root.find("./tei:teiHeader/tei:fileDesc", namespace)
    bibl_struct = (
        file_desc.find("./tei:sourceDesc/tei:biblStruct", namespace)
        if file_desc is not None
        else None
    )
    analytic = bibl_struct.find("./tei:analytic", namespace) if bibl_struct is not None else None
    title_element = (
        file_desc.find("./tei:titleStmt/tei:title", namespace) if file_desc is not None else None
    )
    if title_element is None and analytic is not None:
        title_element = analytic.find("./tei:title[@type='main']", namespace)
    if title_element is None and analytic is not None:
        title_element = analytic.find("./tei:title", namespace)
    title = _element_content(title_element)
    author_elements = (
        file_desc.findall("./tei:titleStmt/tei:author", namespace) if file_desc is not None else []
    )
    if not author_elements and analytic is not None:
        author_elements = analytic.findall("./tei:author", namespace)
    authors = tuple(
        name
        for author in author_elements
        if (name := _grobid_author(author, namespace)) is not None
    )
    doi = _grobid_identifier(bibl_struct, "doi", namespace)
    arxiv_id = _grobid_identifier(bibl_struct, "arxiv", namespace)
    venue = _element_content(
        bibl_struct.find("./tei:monogr/tei:title", namespace) if bibl_struct is not None else None
    )
    date = (
        bibl_struct.find("./tei:monogr/tei:imprint/tei:date", namespace)
        if bibl_struct is not None
        else None
    )
    if date is None and file_desc is not None:
        date = file_desc.find("./tei:publicationStmt/tei:date", namespace)
    year = _year_from_text(date.get("when") or _element_content(date)) if date is not None else None
    values: dict[str, object] = {
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "doi": doi,
        "arxiv_id": arxiv_id,
    }
    return BibliographicMetadata(
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        doi=doi,
        arxiv_id=arxiv_id,
        sources={name: "grobid" for name, value in values.items() if value},
    )


def _grobid_author(author: ET.Element, namespace: dict[str, str]) -> str | None:
    person = author.find("./tei:persName", namespace)
    if person is None:
        return _element_content(author)
    parts = [
        content
        for element in person.findall("./tei:forename", namespace)
        if (content := _element_content(element)) is not None
    ]
    surname = _element_content(person.find("./tei:surname", namespace))
    if surname is not None:
        parts.append(surname)
    return _clean_text(" ".join(parts))


def _grobid_identifier(
    bibl_struct: ET.Element | None,
    identifier_type: str,
    namespace: dict[str, str],
) -> str | None:
    if bibl_struct is None:
        return None
    for element in bibl_struct.findall(".//tei:idno", namespace):
        if element.get("type", "").lower() != identifier_type:
            continue
        value = _element_content(element)
        if value is None:
            return None
        return normalize_doi(value) if identifier_type == "doi" else normalize_arxiv_id(value)
    return None


def _crossref_year(message: dict[str, Any]) -> int | None:
    for field_name in ("published-print", "published-online", "published", "issued"):
        value = message.get(field_name)
        if not isinstance(value, dict):
            continue
        date_parts = value.get("date-parts")
        if (
            isinstance(date_parts, list)
            and date_parts
            and isinstance(date_parts[0], list)
            and date_parts[0]
            and isinstance(date_parts[0][0], int)
        ):
            return date_parts[0][0]
    return None


def _first_string(value: object) -> str | None:
    if not isinstance(value, list) or not value:
        return None
    return _clean_text(value[0])


def _element_text(element: ET.Element, path: str) -> str | None:
    child = element.find(path)
    return _clean_text(child.text) if child is not None else None


def _element_content(element: ET.Element | None) -> str | None:
    return _clean_text("".join(element.itertext())) if element is not None else None


def _clean_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _parse_authors(value: object) -> tuple[str, ...]:
    text = _clean_text(value)
    if text is None:
        return ()
    separator = ";" if ";" in text else " and "
    return tuple(author.strip() for author in text.split(separator) if author.strip())


def _first_text_line(text: str) -> str | None:
    for line in text.splitlines():
        normalized = _clean_text(line)
        if normalized:
            return normalized[:500]
    return None


def _extract_year(value: object) -> int | None:
    text = _clean_text(value)
    if text is None:
        return None
    match = re.search(r"(?:D:)?(?P<year>19\d{2}|20\d{2})", text)
    return int(match.group("year")) if match is not None else None


def _year_from_text(value: str | None) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\b", value or "")
    return int(match.group(0)) if match is not None else None


def _crossref_authors(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    authors: list[str] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get(part, "")).strip() for part in ("given", "family")).strip()
        if name:
            authors.append(name)
    return tuple(authors)


def _http_headers() -> dict[str, str]:
    return {"User-Agent": "Passagen/0.1 (local paper metadata tool)"}
