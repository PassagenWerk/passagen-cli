import hashlib
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from passagen.config import GrobidSettings, ParserBackend, ParsingSettings
from passagen.models import PaperStatus
from passagen.parsing import (
    GrobidFulltextParser,
    PaperParser,
    ParsedPaper,
    ParsingError,
    PyMuPdfParser,
)
from passagen.providers import ProviderHealthSnapshot, ProviderUnavailableError
from passagen.repository import (
    ArtifactRecord,
    PaperRecord,
    get_artifact,
    get_paper,
    save_parsed_artifact,
    update_paper_status,
)
from passagen.stages.progress import ProgressCallback, report_progress

logger = logging.getLogger(__name__)
EXTRACTED_ARTIFACT_KIND = "extracted_json"


class PaperParsingError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class PaperParsingResult:
    paper: PaperRecord
    artifact: ArtifactRecord | None
    parsed: ParsedPaper | None
    warnings: tuple[str, ...] = ()
    updated: bool = True


def parse_paper(
    database_path: Path,
    data_dir: Path,
    paper_id: str,
    settings: ParsingSettings,
    grobid_settings: GrobidSettings,
    *,
    provider_health: ProviderHealthSnapshot | None = None,
    parser: ParserBackend | None = None,
    force: bool = False,
    grobid: GrobidFulltextParser | None = None,
    pymupdf_parser: PaperParser | None = None,
    progress: ProgressCallback | None = None,
) -> PaperParsingResult:
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise PaperParsingError("paper_not_found", f"Paper not found: {paper_id}")
    existing = get_artifact(database_path, paper_id, EXTRACTED_ARTIFACT_KIND)
    if paper.status is not PaperStatus.METADATA_RESOLVED and not force:
        if paper.status in {
            PaperStatus.PARSED,
            PaperStatus.SUMMARIZED,
            PaperStatus.OUTLINED,
        }:
            return PaperParsingResult(paper=paper, artifact=existing, parsed=None, updated=False)
        raise PaperParsingError(
            "metadata_required",
            f"Paper must have resolved metadata before parsing: {paper_id}",
        )
    if force and paper.status is not PaperStatus.METADATA_RESOLVED:
        update_paper_status(database_path, paper_id, PaperStatus.METADATA_RESOLVED)
    if paper.managed_pdf_path is None:
        raise PaperParsingError("missing_pdf", f"Paper has no managed PDF artifact: {paper_id}")
    pdf_path = data_dir / paper.managed_pdf_path
    if not pdf_path.is_file():
        raise PaperParsingError("missing_pdf", f"Managed PDF does not exist: {pdf_path}")

    backend = parser or settings.parser
    grobid_parser = grobid or GrobidFulltextParser(
        base_url=grobid_settings.base_url,
        timeout_seconds=grobid_settings.timeout_seconds,
    )
    fallback_parser = pymupdf_parser or PyMuPdfParser(
        min_text_characters=settings.min_text_characters
    )
    warnings: list[str] = []
    if backend is not ParserBackend.PYMUPDF and provider_health is not None:
        try:
            provider_health.require("grobid")
        except ProviderUnavailableError as exc:
            raise PaperParsingError("grobid_unavailable", str(exc)) from exc
    report_progress(progress, f"Parsing full text with {backend.value}...")
    try:
        parsed = _run_parser(
            backend,
            pdf_path,
            grobid_parser,
            fallback_parser,
            warnings,
            progress,
        )
    except ParsingError as exc:
        logger.error("parse failed: paper_id=%s code=%s error=%s", paper_id, exc.code, exc)
        raise PaperParsingError(exc.code, str(exc)) from exc

    report_progress(progress, "Writing extracted.json...")
    relative_path = Path("papers") / paper_id / "extracted.json"
    content = (parsed.model_dump_json(indent=2) + "\n").encode()
    _atomic_write(data_dir / relative_path, content)
    digest = hashlib.sha256(content).hexdigest()
    updated, artifact = save_parsed_artifact(
        database_path,
        paper_id,
        relative_path,
        version=parsed.schema_version,
        sha256=digest,
        size_bytes=len(content),
        status=PaperStatus.PARSED,
    )
    logger.info(
        "parse finished: paper_id=%s parser=%s sections=%s references=%s artifact=%s",
        paper_id,
        parsed.parser,
        len(parsed.sections),
        len(parsed.references),
        relative_path,
    )
    report_progress(
        progress,
        f"Full text parsed with {parsed.parser}: {len(parsed.sections)} section(s), "
        f"{len(parsed.references)} reference(s).",
    )
    return PaperParsingResult(
        paper=updated,
        artifact=artifact,
        parsed=parsed,
        warnings=tuple(warnings),
    )


def _run_parser(
    backend: ParserBackend,
    path: Path,
    grobid: GrobidFulltextParser,
    pymupdf_parser: PaperParser,
    warnings: list[str],
    progress: ProgressCallback | None,
) -> ParsedPaper:
    if backend is ParserBackend.PYMUPDF:
        return pymupdf_parser.parse(path)
    report_progress(progress, "GROBID is available; parsing TEI full text...")
    return grobid.parse(path)


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="extracted-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
