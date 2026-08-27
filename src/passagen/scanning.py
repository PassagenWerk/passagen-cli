from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from passagen.db import initialize_database
from passagen.models import Paper
from passagen.repository import (
    PaperRecord,
    find_paper_by_sha256,
    managed_path_is_referenced,
    register_pdf,
)

_COPY_CHUNK_SIZE = 1024 * 1024
_PDF_HEADER_SIZE = 1024


class ScanDirectoryError(ValueError):
    pass


class InvalidPdfError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScanFailure:
    path: Path
    message: str


@dataclass(slots=True)
class ScanResult:
    imported: list[PaperRecord] = field(default_factory=list)
    skipped: list[PaperRecord] = field(default_factory=list)
    failures: list[ScanFailure] = field(default_factory=list)


def scan_directory(
    directory: Path,
    *,
    data_dir: Path,
    database_path: Path,
    recursive: bool = True,
) -> ScanResult:
    source_dir = directory.expanduser().resolve()
    managed_root = data_dir.expanduser().resolve()
    if not source_dir.exists():
        raise ScanDirectoryError(f"Scan directory does not exist: {source_dir}")
    if not source_dir.is_dir():
        raise ScanDirectoryError(f"Scan path is not a directory: {source_dir}")

    initialize_database(database_path)
    candidates, discovery_failures = _discover_pdfs(source_dir, managed_root, recursive)
    result = ScanResult(failures=discovery_failures)
    for source_path in candidates:
        try:
            record, created = _import_pdf(source_path, managed_root, database_path)
        except (InvalidPdfError, OSError, RuntimeError, sqlite3.Error) as exc:
            result.failures.append(ScanFailure(source_path, str(exc)))
            continue
        (result.imported if created else result.skipped).append(record)
    return result


def _discover_pdfs(
    source_dir: Path,
    managed_root: Path,
    recursive: bool,
) -> tuple[list[Path], list[ScanFailure]]:
    failures: list[ScanFailure] = []
    candidates: list[Path] = []
    if recursive:

        def on_error(error: OSError) -> None:
            path = Path(error.filename) if error.filename else source_dir
            failures.append(ScanFailure(path, str(error)))

        for root, directories, filenames in os.walk(source_dir, onerror=on_error):
            root_path = Path(root)
            directories[:] = [
                name
                for name in directories
                if not (root_path / name).resolve().is_relative_to(managed_root)
            ]
            candidates.extend(
                root_path / name for name in filenames if Path(name).suffix.lower() == ".pdf"
            )
    else:
        try:
            candidates = [
                path
                for path in source_dir.iterdir()
                if path.is_file()
                and path.suffix.lower() == ".pdf"
                and not path.resolve().is_relative_to(managed_root)
            ]
        except OSError as exc:
            failures.append(ScanFailure(source_dir, str(exc)))

    return sorted(candidates), failures


def _import_pdf(
    source_path: Path,
    data_dir: Path,
    database_path: Path,
) -> tuple[PaperRecord, bool]:
    temp_path, sha256, size_bytes = _copy_to_temp(source_path, data_dir)
    managed_path = Path("pdfs") / sha256[:2] / f"{sha256}.pdf"
    destination = data_dir / managed_path
    created_file = False
    try:
        existing = find_paper_by_sha256(database_path, sha256)
        if destination.exists() and _sha256(destination) != sha256:
            os.replace(temp_path, destination)
            temp_path = None
        elif not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            os.replace(temp_path, destination)
            temp_path = None
            created_file = True

        if existing is not None:
            return existing, False

        paper = Paper(
            original_filename=source_path.name,
            pdf_sha256=sha256,
            file_size_bytes=size_bytes,
        )
        return register_pdf(database_path, paper, managed_path)
    except Exception:
        if created_file and not managed_path_is_referenced(database_path, managed_path):
            destination.unlink(missing_ok=True)
        raise
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _copy_to_temp(source_path: Path, data_dir: Path) -> tuple[Path, str, int]:
    temp_dir = data_dir / "pdfs" / ".tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix="import-", suffix=".tmp", dir=temp_dir)
    temp_path = Path(temp_name)
    digest = hashlib.sha256()
    header = bytearray()
    tail = bytearray()
    size_bytes = 0
    try:
        with os.fdopen(descriptor, "wb") as destination:
            descriptor = -1
            with source_path.open("rb") as source:
                while chunk := source.read(_COPY_CHUNK_SIZE):
                    if len(header) < _PDF_HEADER_SIZE:
                        header.extend(chunk[: _PDF_HEADER_SIZE - len(header)])
                    tail.extend(chunk)
                    if len(tail) > _PDF_HEADER_SIZE:
                        del tail[:-_PDF_HEADER_SIZE]
                    digest.update(chunk)
                    destination.write(chunk)
                    size_bytes += len(chunk)
                destination.flush()
                os.fsync(destination.fileno())
    except Exception:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
        raise

    if b"%PDF-" not in header:
        temp_path.unlink(missing_ok=True)
        raise InvalidPdfError(f"File does not contain a PDF header: {source_path}")
    if b"%%EOF" not in tail:
        temp_path.unlink(missing_ok=True)
        raise InvalidPdfError(f"File does not contain a PDF trailer: {source_path}")
    return temp_path, digest.hexdigest(), size_bytes


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(_COPY_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()
