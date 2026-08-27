import re
import statistics
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Protocol

import httpx
import pymupdf
from pydantic import BaseModel, ConfigDict, Field

from passagen.metadata import extract_doi, normalize_arxiv_id, normalize_doi

_TEI = "http://www.tei-c.org/ns/1.0"
_NS = {"tei": _TEI}
_HEADING_PATTERN = re.compile(r"^(?:\d+(?:\.\d+)*\s+|abstract$|references$)", re.IGNORECASE)
_COORD_PAGE_PATTERN = re.compile(r"(?:^|;)(?P<page>\d+),")


class ParsingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ParsedMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


class ParsedSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    text: str
    pages: tuple[int, ...] = ()


class ParsedReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    raw_text: str
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    doi: str | None = None


class ParsedPaper(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = "1"
    metadata: ParsedMetadata = Field(default_factory=ParsedMetadata)
    sections: tuple[ParsedSection, ...]
    references: tuple[ParsedReference, ...] = ()
    parser: str


class PaperParser(Protocol):
    name: str

    def parse(self, path: Path) -> ParsedPaper: ...


class GrobidFulltextParser:
    name = "grobid"

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

    def parse(self, path: Path) -> ParsedPaper:
        try:
            with path.open("rb") as pdf_file:
                response = self._post(
                    f"{self.base_url}/api/processFulltextDocument",
                    files={"input": (path.name, pdf_file, "application/pdf")},
                    data={
                        "consolidateHeader": "0",
                        "consolidateCitations": "0",
                        "includeRawCitations": "1",
                        "teiCoordinates": ["head", "p", "biblStruct"],
                    },
                )
            response.raise_for_status()
            root = ET.fromstring(response.content)
        except OSError as exc:
            raise ParsingError("pdf_read_error", f"Cannot read PDF {path}: {exc}") from exc
        except httpx.HTTPError as exc:
            raise ParsingError("grobid_unavailable", f"GROBID parsing failed: {exc}") from exc
        except ET.ParseError as exc:
            raise ParsingError("invalid_tei", f"GROBID returned invalid TEI: {exc}") from exc

        sections = _tei_sections(root)
        if not sections:
            raise ParsingError("insufficient_text", "GROBID returned no body sections")
        return ParsedPaper(
            metadata=_tei_metadata(root),
            sections=tuple(sections),
            references=tuple(_tei_references(root)),
            parser=self.name,
        )

    def _get(self, url: str) -> httpx.Response:
        if self.client is not None:
            return self.client.get(url)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.get(url)

    def _post(
        self,
        url: str,
        *,
        files: dict[str, tuple[str, Any, str]],
        data: dict[str, str | list[str]],
    ) -> httpx.Response:
        if self.client is not None:
            return self.client.post(url, files=files, data=data)
        with httpx.Client(timeout=self.timeout_seconds) as client:
            return client.post(url, files=files, data=data)


class PyMuPdfParser:
    name = "pymupdf"

    def __init__(self, *, min_text_characters: int) -> None:
        self.min_text_characters = min_text_characters

    def parse(self, path: Path) -> ParsedPaper:
        try:
            with pymupdf.open(path) as document:
                if document.needs_pass:
                    raise ParsingError("encrypted_pdf", f"PDF is encrypted: {path}")
                raw_metadata = document.metadata or {}
                page_lines = [
                    _page_lines(document[index], index + 1) for index in range(document.page_count)
                ]
        except ParsingError:
            raise
        except (OSError, RuntimeError, ValueError) as exc:
            raise ParsingError("pdf_read_error", f"Cannot parse PDF {path}: {exc}") from exc

        all_text = "\n".join(line[0] for lines in page_lines for line in lines).strip()
        if not all_text:
            raise ParsingError("no_text_layer", f"PDF has no extractable text layer: {path}")
        if len(all_text) < self.min_text_characters:
            raise ParsingError(
                "insufficient_text",
                f"Extracted text is shorter than {self.min_text_characters} characters",
            )
        title = _clean(raw_metadata.get("title")) or _first_line(all_text)
        authors = _split_authors(raw_metadata.get("author"))
        return ParsedPaper(
            metadata=ParsedMetadata(
                title=title,
                authors=authors,
                year=_year(_clean(raw_metadata.get("creationDate"))),
                doi=extract_doi(all_text),
            ),
            sections=tuple(_layout_sections(page_lines)),
            parser=self.name,
        )


def _page_lines(page: pymupdf.Page, page_number: int) -> list[tuple[str, float, int]]:
    result: list[tuple[str, float, int]] = []
    document: Any = page.get_text("dict")
    for block in document.get("blocks", []):
        if not isinstance(block, dict):
            continue
        for line in block.get("lines", []):
            spans = line.get("spans", []) if isinstance(line, dict) else []
            text = _clean(
                " ".join(str(span.get("text", "")) for span in spans if isinstance(span, dict))
            )
            sizes = [float(span.get("size", 0)) for span in spans if isinstance(span, dict)]
            if text:
                result.append((text, max(sizes, default=0), page_number))
    return result


def _layout_sections(page_lines: list[list[tuple[str, float, int]]]) -> list[ParsedSection]:
    lines = [line for page in page_lines for line in page]
    sizes = [size for _, size, _ in lines if size > 0]
    body_size = statistics.median(sizes) if sizes else 10.0
    sections: list[ParsedSection] = []
    title: str | None = None
    text_lines: list[str] = []
    pages: set[int] = set()

    def flush() -> None:
        if text_lines:
            sections.append(
                ParsedSection(title=title, text="\n".join(text_lines), pages=tuple(sorted(pages)))
            )

    for text, size, page in lines:
        is_heading = len(text) <= 180 and (
            size >= body_size * 1.25 or (_HEADING_PATTERN.match(text) and size >= body_size)
        )
        if is_heading and text_lines:
            flush()
            title = text
            text_lines = []
            pages = set()
        elif is_heading:
            title = text
        else:
            text_lines.append(text)
            pages.add(page)
    flush()
    return sections or [
        ParsedSection(
            text="\n".join(text for text, _, _ in lines),
            pages=tuple(range(1, len(page_lines) + 1)),
        )
    ]


def _tei_metadata(root: ET.Element) -> ParsedMetadata:
    title = _content(root.find("./tei:teiHeader/tei:fileDesc/tei:titleStmt/tei:title", _NS))
    analytic = root.find(
        "./tei:teiHeader/tei:fileDesc/tei:sourceDesc/tei:biblStruct/tei:analytic",
        _NS,
    )
    if title is None and analytic is not None:
        title = _content(analytic.find("./tei:title", _NS))
    authors = tuple(
        _person_name(author) for author in root.findall(".//tei:titleStmt/tei:author", _NS)
    )
    if not authors and analytic is not None:
        authors = tuple(_person_name(author) for author in analytic.findall("./tei:author", _NS))
    authors = tuple(author for author in authors if author)
    bibl = root.find("./tei:teiHeader/tei:fileDesc/tei:sourceDesc/tei:biblStruct", _NS)
    venue = _content(bibl.find("./tei:monogr/tei:title", _NS)) if bibl is not None else None
    date = bibl.find("./tei:monogr/tei:imprint/tei:date", _NS) if bibl is not None else None
    doi = _tei_idno(bibl, "doi")
    arxiv_id = _tei_idno(bibl, "arxiv")
    return ParsedMetadata(
        title=title,
        authors=authors,
        year=_year(date.get("when") if date is not None else None),
        venue=venue,
        doi=normalize_doi(doi) if doi else None,
        arxiv_id=normalize_arxiv_id(arxiv_id) if arxiv_id else None,
    )


def _tei_sections(root: ET.Element) -> list[ParsedSection]:
    sections: list[ParsedSection] = []
    for division in root.findall("./tei:text/tei:body//tei:div", _NS):
        paragraphs = division.findall("./tei:p", _NS)
        text = "\n".join(content for paragraph in paragraphs if (content := _content(paragraph)))
        if not text:
            continue
        pages = _pages([division.find("./tei:head", _NS), *paragraphs])
        sections.append(
            ParsedSection(
                title=_content(division.find("./tei:head", _NS)),
                text=text,
                pages=pages,
            )
        )
    return sections


def _tei_references(root: ET.Element) -> list[ParsedReference]:
    references: list[ParsedReference] = []
    for bibl in root.findall(".//tei:listBibl/tei:biblStruct", _NS):
        raw = _content(bibl.find("./tei:note[@type='raw_reference']", _NS)) or _content(bibl)
        if not raw:
            continue
        authors = tuple(
            name for author in bibl.findall(".//tei:author", _NS) if (name := _person_name(author))
        )
        date = bibl.find(".//tei:date", _NS)
        doi = _tei_idno(bibl, "doi")
        references.append(
            ParsedReference(
                raw_text=raw,
                title=_content(bibl.find(".//tei:title[@level='a']", _NS)),
                authors=authors,
                year=_year(date.get("when") if date is not None else None),
                doi=normalize_doi(doi) if doi else None,
            )
        )
    return references


def _pages(elements: list[ET.Element | None]) -> tuple[int, ...]:
    pages: set[int] = set()
    for element in elements:
        if element is None:
            continue
        for match in _COORD_PAGE_PATTERN.finditer(element.get("coords", "")):
            pages.add(int(match.group("page")))
    return tuple(sorted(pages))


def _tei_idno(element: ET.Element | None, kind: str) -> str | None:
    if element is None:
        return None
    for idno in element.findall(".//tei:idno", _NS):
        if idno.get("type", "").lower() == kind:
            return _content(idno)
    return None


def _person_name(author: ET.Element) -> str:
    person = author.find("./tei:persName", _NS)
    if person is None:
        return _content(author) or ""
    parts = [_content(item) for item in person.findall("./tei:forename", _NS)]
    parts.append(_content(person.find("./tei:surname", _NS)))
    return " ".join(part for part in parts if part)


def _content(element: ET.Element | None) -> str | None:
    return _clean("".join(element.itertext())) if element is not None else None


def _clean(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    result = " ".join(value.split())
    return result or None


def _year(value: str | None) -> int | None:
    match = re.search(r"\b(19\d{2}|20\d{2})\b", value or "")
    return int(match.group(0)) if match else None


def _split_authors(value: object) -> tuple[str, ...]:
    text = _clean(value)
    if text is None:
        return ()
    separator = ";" if ";" in text else " and "
    return tuple(author.strip() for author in text.split(separator) if author.strip())


def _first_line(text: str) -> str | None:
    return _clean(text.splitlines()[0]) if text else None
