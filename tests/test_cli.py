from pathlib import Path

import pytest
from typer.testing import CliRunner

from passagen.cli import app
from passagen.repository import list_papers

runner = CliRunner()


def test_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage paper PDFs" in result.stdout


def test_database_init_uses_current_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "data" / "passagen.db").exists()


def test_database_init_and_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "new" / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0
    assert (data_dir / "passagen.db").exists()

    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "status"])
    assert result.exit_code == 0
    assert "schema version: 2" in result.stdout


def test_scan_list_and_show(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    source_dir.mkdir()
    (source_dir / "paper.pdf").write_bytes(b"%PDF-1.7\ncontent\n%%EOF\n")
    data_dir = tmp_path / "data"

    result = runner.invoke(app, ["--data-dir", str(data_dir), "scan", str(source_dir)])

    assert result.exit_code == 0
    assert "Imported: 1, skipped: 0, failed: 0" in result.stdout

    papers = list_papers(data_dir / "passagen.db")
    assert len(papers) == 1
    assert papers[0].managed_pdf_path is not None

    result = runner.invoke(app, ["--data-dir", str(data_dir), "list"])
    assert result.exit_code == 0
    assert papers[0].id in result.stdout
    assert "paper.pdf" in result.stdout
    assert "discovered" in result.stdout

    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "list", "--status", "parsed"],
    )
    assert result.exit_code == 0
    assert papers[0].id not in result.stdout

    result = runner.invoke(app, ["--data-dir", str(data_dir), "show", papers[0].id])
    assert result.exit_code == 0
    assert papers[0].pdf_sha256 in result.stdout
    assert str(data_dir / papers[0].managed_pdf_path) in result.stdout


def test_scan_reports_invalid_pdf_without_stopping(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    source_dir.mkdir()
    (source_dir / "invalid.pdf").write_text("not a PDF")
    (source_dir / "valid.pdf").write_bytes(b"%PDF-1.7\ncontent\n%%EOF\n")
    data_dir = tmp_path / "data"

    result = runner.invoke(app, ["--data-dir", str(data_dir), "scan", str(source_dir)])

    assert result.exit_code == 1
    assert "invalid.pdf" in result.stdout
    assert "Imported: 1, skipped: 0, failed: 1" in result.stdout


def test_list_rejects_uninitialized_database(tmp_path: Path) -> None:
    result = runner.invoke(app, ["--data-dir", str(tmp_path / "data"), "list"])

    assert result.exit_code == 1
    assert "Database is not initialized" in result.stdout


def test_show_rejects_unknown_paper(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["--data-dir", str(data_dir), "show", "missing"])

    assert result.exit_code == 1
    assert "Paper not found" in result.stdout
