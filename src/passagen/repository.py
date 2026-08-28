from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from passagen.db import connect_database
from passagen.metadata import BibliographicMetadata
from passagen.models import Paper, PaperStatus


class DatabaseNotInitializedError(RuntimeError):
    pass


class MetadataConflictError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRecord:
    id: str
    original_filename: str
    pdf_sha256: str
    status: PaperStatus
    title: str | None
    authors: tuple[str, ...]
    year: int | None
    venue: str | None
    doi: str | None
    arxiv_id: str | None
    source_url: str | None
    metadata_sources: dict[str, str]
    managed_pdf_path: Path | None
    file_size_bytes: int | None
    imported_at: str


@dataclass(frozen=True, slots=True)
class ArtifactRecord:
    id: str
    paper_id: str
    kind: str
    path: Path
    version: str | None
    sha256: str | None
    size_bytes: int | None


def register_pdf(
    database_path: Path,
    paper: Paper,
    managed_path: Path,
) -> tuple[PaperRecord, bool]:
    with connect_database(database_path) as connection:
        cursor = connection.execute(
            """
            INSERT INTO papers (id, original_filename, pdf_sha256, status)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(pdf_sha256) DO NOTHING
            """,
            (
                paper.id,
                paper.original_filename,
                paper.pdf_sha256,
                paper.status.value,
            ),
        )
        created = cursor.rowcount == 1
        if created:
            connection.execute(
                """
                INSERT INTO artifacts (
                    id, paper_id, kind, path, sha256, size_bytes
                ) VALUES (?, ?, 'original_pdf', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    paper.id,
                    managed_path.as_posix(),
                    paper.pdf_sha256,
                    paper.file_size_bytes,
                ),
            )

        row = _select_paper(connection, "p.pdf_sha256 = ?", (paper.pdf_sha256,))
        if row is None:
            raise RuntimeError(f"Failed to register PDF {paper.pdf_sha256}")
        return _paper_record(row), created


def find_paper_by_sha256(database_path: Path, sha256: str) -> PaperRecord | None:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        row = _select_paper(connection, "p.pdf_sha256 = ?", (sha256,))
    return _paper_record(row) if row is not None else None


def list_papers(
    database_path: Path,
    status: PaperStatus | None = None,
) -> list[PaperRecord]:
    _require_database(database_path)
    parameters: tuple[str, ...] = ()
    where = ""
    if status is not None:
        where = "WHERE p.status = ?"
        parameters = (status.value,)

    with connect_database(database_path) as connection:
        rows = connection.execute(
            f"""
            {_PAPER_SELECT}
            {where}
            ORDER BY p.created_at, p.id
            """,
            parameters,
        ).fetchall()
    return [_paper_record(row) for row in rows]


def get_paper(database_path: Path, paper_id: str) -> PaperRecord | None:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        row = _select_paper(connection, "p.id = ?", (paper_id,))
    return _paper_record(row) if row is not None else None


def update_paper_metadata(
    database_path: Path,
    paper_id: str,
    metadata: BibliographicMetadata,
    status: PaperStatus,
) -> PaperRecord:
    _require_database(database_path)
    try:
        with connect_database(database_path) as connection:
            cursor = connection.execute(
                """
                UPDATE papers
                SET title = ?, authors_json = ?, year = ?, venue = ?, doi = ?,
                    arxiv_id = ?, source_url = ?, metadata_sources_json = ?,
                    status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    metadata.title,
                    json.dumps(metadata.authors, ensure_ascii=False),
                    metadata.year,
                    metadata.venue,
                    metadata.doi,
                    metadata.arxiv_id,
                    metadata.source_url,
                    json.dumps(metadata.sources, ensure_ascii=False, sort_keys=True),
                    status.value,
                    paper_id,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(paper_id)
            row = _select_paper(connection, "p.id = ?", (paper_id,))
    except sqlite3.IntegrityError as exc:
        raise MetadataConflictError(
            f"DOI or arXiv ID is already assigned to another paper: {exc}"
        ) from exc
    if row is None:
        raise RuntimeError(f"Failed to reload paper {paper_id}")
    return _paper_record(row)


def get_artifact(
    database_path: Path,
    paper_id: str,
    kind: str,
) -> ArtifactRecord | None:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        row = connection.execute(
            "SELECT id, paper_id, kind, path, version, sha256, size_bytes "
            "FROM artifacts WHERE paper_id = ? AND kind = ? "
            "ORDER BY created_at DESC LIMIT 1",
            (paper_id, kind),
        ).fetchone()
    return _artifact_record(row) if row is not None else None


def list_artifacts(database_path: Path) -> list[ArtifactRecord]:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        rows = connection.execute(
            "SELECT id, paper_id, kind, path, version, sha256, size_bytes "
            "FROM artifacts ORDER BY created_at, id"
        ).fetchall()
    return [_artifact_record(row) for row in rows]


def save_parsed_artifact(
    database_path: Path,
    paper_id: str,
    path: Path,
    *,
    version: str,
    sha256: str,
    size_bytes: int,
    status: PaperStatus,
) -> tuple[PaperRecord, ArtifactRecord]:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        row = connection.execute(
            "SELECT id FROM artifacts WHERE paper_id = ? AND kind = 'extracted_json' "
            "ORDER BY created_at DESC LIMIT 1",
            (paper_id,),
        ).fetchone()
        artifact_id = str(row["id"]) if row is not None else str(uuid.uuid4())
        if row is None:
            connection.execute(
                "INSERT INTO artifacts "
                "(id, paper_id, kind, path, version, sha256, size_bytes) "
                "VALUES (?, ?, 'extracted_json', ?, ?, ?, ?)",
                (artifact_id, paper_id, path.as_posix(), version, sha256, size_bytes),
            )
        else:
            connection.execute(
                "UPDATE artifacts SET path = ?, version = ?, sha256 = ?, size_bytes = ?, "
                "created_at = CURRENT_TIMESTAMP WHERE id = ?",
                (path.as_posix(), version, sha256, size_bytes, artifact_id),
            )
        cursor = connection.execute(
            "UPDATE papers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status.value, paper_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(paper_id)
        paper_row = _select_paper(connection, "p.id = ?", (paper_id,))
        artifact_row = connection.execute(
            "SELECT id, paper_id, kind, path, version, sha256, size_bytes "
            "FROM artifacts WHERE id = ?",
            (artifact_id,),
        ).fetchone()
    if paper_row is None or artifact_row is None:
        raise RuntimeError(f"Failed to reload parsed artifact for {paper_id}")
    return _paper_record(paper_row), _artifact_record(artifact_row)


def save_summary_artifacts(
    database_path: Path,
    paper_id: str,
    json_path: Path,
    yaml_path: Path,
    *,
    version: str,
    json_sha256: str,
    json_size_bytes: int,
    yaml_sha256: str,
    yaml_size_bytes: int,
) -> tuple[PaperRecord, ArtifactRecord]:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        summary_artifact = _upsert_artifact(
            connection,
            paper_id,
            "summary_json",
            json_path,
            version,
            json_sha256,
            json_size_bytes,
        )
        _upsert_artifact(
            connection,
            paper_id,
            "summary_yaml",
            yaml_path,
            version,
            yaml_sha256,
            yaml_size_bytes,
        )
        cursor = connection.execute(
            "UPDATE papers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (PaperStatus.SUMMARIZED.value, paper_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(paper_id)
        paper_row = _select_paper(connection, "p.id = ?", (paper_id,))
    if paper_row is None:
        raise RuntimeError(f"Failed to reload summary artifact for {paper_id}")
    return _paper_record(paper_row), summary_artifact


def save_outline_artifacts(
    database_path: Path,
    paper_id: str,
    markdown_path: Path,
    source_path: Path,
    *,
    version: str,
    markdown_sha256: str,
    markdown_size_bytes: int,
    source_sha256: str,
    source_size_bytes: int,
) -> tuple[PaperRecord, ArtifactRecord]:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        outline_artifact = _upsert_artifact(
            connection,
            paper_id,
            "outline_md",
            markdown_path,
            version,
            markdown_sha256,
            markdown_size_bytes,
        )
        _upsert_artifact(
            connection,
            paper_id,
            "outline_source_json",
            source_path,
            version,
            source_sha256,
            source_size_bytes,
        )
        cursor = connection.execute(
            "UPDATE papers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (PaperStatus.OUTLINED.value, paper_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(paper_id)
        paper_row = _select_paper(connection, "p.id = ?", (paper_id,))
    if paper_row is None:
        raise RuntimeError(f"Failed to reload outline artifact for {paper_id}")
    return _paper_record(paper_row), outline_artifact


def start_processing_run(database_path: Path, paper_id: str, stage: str) -> str:
    _require_database(database_path)
    run_id = str(uuid.uuid4())
    with connect_database(database_path) as connection:
        connection.execute(
            "INSERT INTO processing_runs (id, paper_id, stage, status) VALUES (?, ?, ?, 'running')",
            (run_id, paper_id, stage),
        )
    return run_id


def finish_processing_run(
    database_path: Path,
    run_id: str,
    *,
    error_message: str | None = None,
) -> None:
    with connect_database(database_path) as connection:
        connection.execute(
            "UPDATE processing_runs SET status = ?, error_message = ?, "
            "finished_at = CURRENT_TIMESTAMP "
            "WHERE id = ?",
            ("failed" if error_message else "completed", error_message, run_id),
        )


def update_paper_status(database_path: Path, paper_id: str, status: PaperStatus) -> None:
    _require_database(database_path)
    with connect_database(database_path) as connection:
        cursor = connection.execute(
            "UPDATE papers SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            (status.value, paper_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(paper_id)


def record_llm_call(
    database_path: Path,
    processing_run_id: str,
    *,
    provider: str,
    model: str,
    prompt_version: str,
    schema_version: str,
    input_tokens: int | None,
    output_tokens: int | None,
    error_message: str | None = None,
) -> None:
    with connect_database(database_path) as connection:
        connection.execute(
            "INSERT INTO llm_calls (id, processing_run_id, provider, model, prompt_version, "
            "schema_version, input_tokens, output_tokens, error_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(uuid.uuid4()),
                processing_run_id,
                provider,
                model,
                prompt_version,
                schema_version,
                input_tokens,
                output_tokens,
                error_message,
            ),
        )


def _upsert_artifact(
    connection: sqlite3.Connection,
    paper_id: str,
    kind: str,
    path: Path,
    version: str,
    sha256: str,
    size_bytes: int,
) -> ArtifactRecord:
    row = connection.execute(
        "SELECT id FROM artifacts WHERE paper_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
        (paper_id, kind),
    ).fetchone()
    artifact_id = str(row["id"]) if row is not None else str(uuid.uuid4())
    if row is None:
        connection.execute(
            "INSERT INTO artifacts (id, paper_id, kind, path, version, sha256, size_bytes) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (artifact_id, paper_id, kind, path.as_posix(), version, sha256, size_bytes),
        )
    else:
        connection.execute(
            "UPDATE artifacts SET path = ?, version = ?, sha256 = ?, size_bytes = ?, "
            "created_at = CURRENT_TIMESTAMP WHERE id = ?",
            (path.as_posix(), version, sha256, size_bytes, artifact_id),
        )
    artifact_row = connection.execute(
        "SELECT id, paper_id, kind, path, version, sha256, size_bytes FROM artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if artifact_row is None:
        raise RuntimeError(f"Failed to reload {kind} artifact for {paper_id}")
    return _artifact_record(artifact_row)


def managed_path_is_referenced(database_path: Path, managed_path: Path) -> bool:
    if not database_path.exists():
        return False
    with connect_database(database_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM artifacts WHERE path = ? LIMIT 1",
            (managed_path.as_posix(),),
        ).fetchone()
    return row is not None


_PAPER_SELECT = """
    SELECT
        p.id,
        p.original_filename,
        p.pdf_sha256,
        p.status,
        p.title,
        p.authors_json,
        p.year,
        p.venue,
        p.doi,
        p.arxiv_id,
        p.source_url,
        p.metadata_sources_json,
        p.created_at AS imported_at,
        a.path AS managed_pdf_path,
        a.size_bytes
    FROM papers AS p
    LEFT JOIN artifacts AS a
        ON a.paper_id = p.id AND a.kind = 'original_pdf'
"""


def _select_paper(
    connection: sqlite3.Connection,
    condition: str,
    parameters: tuple[str, ...],
) -> sqlite3.Row | None:
    return connection.execute(
        f"{_PAPER_SELECT} WHERE {condition} LIMIT 1",
        parameters,
    ).fetchone()


def _paper_record(row: sqlite3.Row) -> PaperRecord:
    managed_path = row["managed_pdf_path"]
    authors = _json_list(row["authors_json"])
    sources = _json_dict(row["metadata_sources_json"])
    return PaperRecord(
        id=str(row["id"]),
        original_filename=str(row["original_filename"]),
        pdf_sha256=str(row["pdf_sha256"]),
        status=PaperStatus(row["status"]),
        title=str(row["title"]) if row["title"] is not None else None,
        authors=tuple(str(author) for author in authors),
        year=int(row["year"]) if row["year"] is not None else None,
        venue=str(row["venue"]) if row["venue"] is not None else None,
        doi=str(row["doi"]) if row["doi"] is not None else None,
        arxiv_id=str(row["arxiv_id"]) if row["arxiv_id"] is not None else None,
        source_url=str(row["source_url"]) if row["source_url"] is not None else None,
        metadata_sources={str(key): str(value) for key, value in sources.items()},
        managed_pdf_path=Path(managed_path) if managed_path is not None else None,
        file_size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
        imported_at=str(row["imported_at"]),
    )


def _artifact_record(row: sqlite3.Row) -> ArtifactRecord:
    return ArtifactRecord(
        id=str(row["id"]),
        paper_id=str(row["paper_id"]),
        kind=str(row["kind"]),
        path=Path(str(row["path"])),
        version=str(row["version"]) if row["version"] is not None else None,
        sha256=str(row["sha256"]) if row["sha256"] is not None else None,
        size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
    )


def _require_database(database_path: Path) -> None:
    if not database_path.exists():
        raise DatabaseNotInitializedError(f"Database is not initialized: {database_path}")


def _json_list(value: object) -> list[object]:
    if not isinstance(value, str) or not value:
        return []
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def _json_dict(value: object) -> dict[object, object]:
    if not isinstance(value, str) or not value:
        return {}
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}
