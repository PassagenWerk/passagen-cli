from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1

MIGRATIONS = {
    1: """
        CREATE TABLE papers (
            id TEXT PRIMARY KEY,
            title TEXT,
            authors_json TEXT,
            year INTEGER,
            venue TEXT,
            doi TEXT,
            arxiv_id TEXT,
            source_url TEXT,
            original_filename TEXT NOT NULL,
            pdf_sha256 TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'discovered' CHECK (
                status IN (
                    'discovered', 'parsed', 'metadata_resolved', 'summarized',
                    'outlined', 'completed', 'failed'
                )
            ),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX ux_papers_doi ON papers(doi);
        CREATE UNIQUE INDEX ux_papers_arxiv_id ON papers(arxiv_id);
        CREATE UNIQUE INDEX ux_papers_pdf_sha256 ON papers(pdf_sha256);

        CREATE TABLE artifacts (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            version TEXT,
            sha256 TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_artifacts_paper_id ON artifacts(paper_id);

        CREATE TABLE processing_runs (
            id TEXT PRIMARY KEY,
            paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
            stage TEXT NOT NULL,
            status TEXT NOT NULL,
            error_code TEXT,
            error_message TEXT,
            started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            finished_at TEXT
        );
        CREATE INDEX ix_processing_runs_paper_id ON processing_runs(paper_id);

        CREATE TABLE llm_calls (
            id TEXT PRIMARY KEY,
            processing_run_id TEXT NOT NULL
                REFERENCES processing_runs(id) ON DELETE CASCADE,
            provider TEXT NOT NULL,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            schema_version TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            error_message TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX ix_llm_calls_processing_run_id ON llm_calls(processing_run_id);
    """,
}


class DatabaseVersionError(RuntimeError):
    pass


def initialize_database(database_path: Path) -> None:
    with connect_database(database_path) as connection:
        current_version = _schema_version(connection)
        if current_version > SCHEMA_VERSION:
            raise DatabaseVersionError(
                f"Database schema {current_version} is newer than supported {SCHEMA_VERSION}"
            )

        for target_version in range(current_version + 1, SCHEMA_VERSION + 1):
            _apply_migration(connection, target_version)


def current_version(database_path: Path) -> int | None:
    if not database_path.exists():
        return None
    with connect_database(database_path) as connection:
        return _schema_version(connection)


@contextmanager
def connect_database(database_path: Path) -> Iterator[sqlite3.Connection]:
    database_path = database_path.expanduser().resolve()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 5000")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _apply_migration(connection: sqlite3.Connection, target_version: int) -> None:
    migration = MIGRATIONS.get(target_version)
    if migration is None:
        raise DatabaseVersionError(f"Missing database migration {target_version}")

    try:
        connection.executescript(
            f"BEGIN IMMEDIATE;\n{migration}\nPRAGMA user_version = {target_version};\nCOMMIT;"
        )
    except sqlite3.Error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise


def _schema_version(connection: sqlite3.Connection) -> int:
    row = connection.execute("PRAGMA user_version").fetchone()
    return int(row[0])
