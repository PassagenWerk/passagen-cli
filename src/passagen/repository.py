from __future__ import annotations

import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

from passagen.db import connect_database
from passagen.models import Paper, PaperStatus


class DatabaseNotInitializedError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class PaperRecord:
    id: str
    original_filename: str
    pdf_sha256: str
    status: PaperStatus
    title: str | None
    year: int | None
    managed_pdf_path: Path | None
    file_size_bytes: int | None
    imported_at: str


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
        p.year,
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
    return PaperRecord(
        id=str(row["id"]),
        original_filename=str(row["original_filename"]),
        pdf_sha256=str(row["pdf_sha256"]),
        status=PaperStatus(row["status"]),
        title=str(row["title"]) if row["title"] is not None else None,
        year=int(row["year"]) if row["year"] is not None else None,
        managed_pdf_path=Path(managed_path) if managed_path is not None else None,
        file_size_bytes=int(row["size_bytes"]) if row["size_bytes"] is not None else None,
        imported_at=str(row["imported_at"]),
    )


def _require_database(database_path: Path) -> None:
    if not database_path.exists():
        raise DatabaseNotInitializedError(f"Database is not initialized: {database_path}")
