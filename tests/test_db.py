from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from passagen.db import (
    SCHEMA_VERSION,
    DatabaseVersionError,
    connect_database,
    current_version,
    initialize_database,
)


def insert_paper(connection: sqlite3.Connection, paper_id: str, sha256: str) -> None:
    connection.execute(
        """
        INSERT INTO papers (id, original_filename, pdf_sha256)
        VALUES (?, ?, ?)
        """,
        (paper_id, "paper.pdf", sha256),
    )


def test_initialize_database_creates_current_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "passagen.db"

    initialize_database(database_path)
    initialize_database(database_path)

    assert current_version(database_path) == SCHEMA_VERSION
    with connect_database(database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        paper_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(papers)").fetchall()
        }
        artifact_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(artifacts)").fetchall()
        }
    assert {"papers", "artifacts", "processing_runs", "llm_calls"} <= tables
    assert "metadata_sources_json" in paper_columns
    assert "size_bytes" in artifact_columns


def test_sha256_is_unique(tmp_path: Path) -> None:
    database_path = tmp_path / "passagen.db"
    initialize_database(database_path)

    with pytest.raises(sqlite3.IntegrityError), connect_database(database_path) as connection:
        insert_paper(connection, "paper-1", "a" * 64)
        insert_paper(connection, "paper-2", "a" * 64)


def test_managed_pdf_is_recorded_as_relative_artifact_path(tmp_path: Path) -> None:
    database_path = tmp_path / "passagen.db"
    initialize_database(database_path)

    sha256 = "a" * 64
    managed_path = f"pdfs/{sha256[:2]}/{sha256}.pdf"
    with connect_database(database_path) as connection:
        insert_paper(connection, "paper-1", sha256)
        connection.execute(
            """
            INSERT INTO artifacts (id, paper_id, kind, path, sha256)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("pdf-1", "paper-1", "original_pdf", managed_path, sha256),
        )

        row = connection.execute("SELECT path FROM artifacts WHERE id = ?", ("pdf-1",)).fetchone()

    assert row is not None
    assert row["path"] == managed_path


def test_foreign_keys_are_enabled(tmp_path: Path) -> None:
    database_path = tmp_path / "passagen.db"
    initialize_database(database_path)

    with pytest.raises(sqlite3.IntegrityError), connect_database(database_path) as connection:
        connection.execute(
            "INSERT INTO artifacts (id, paper_id, kind, path) VALUES (?, ?, ?, ?)",
            ("artifact-1", "missing-paper", "pdf", "/paper.pdf"),
        )


def test_rejects_newer_database_schema(tmp_path: Path) -> None:
    database_path = tmp_path / "passagen.db"
    with connect_database(database_path) as connection:
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION + 1}")

    with pytest.raises(DatabaseVersionError):
        initialize_database(database_path)
