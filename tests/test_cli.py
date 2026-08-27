from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from passagen.cli import app
from passagen.repository import list_papers

runner = CliRunner()


def write_metadata_pdf(path: Path, title: str, text: str = "Paper body") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), text)
        document.set_metadata({"title": title, "author": "Test Author"})
        document.save(path)


def write_offline_config(path: Path) -> None:
    path.write_text(
        """
passagen: {}
metadata:
  crossref:
    enabled: false
  arxiv:
    enabled: false
"""
    )


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
    assert "schema version: 1" in result.stdout


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


def test_metadata_command_resolves_local_pdf_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    pdf_path = source_dir / "paper.pdf"
    write_metadata_pdf(pdf_path, "Local Metadata Title", "DOI: 10.1000/local")
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir)]
    result = runner.invoke(app, [*common, "scan", str(source_dir)])
    assert result.exit_code == 0
    paper = list_papers(data_dir / "passagen.db")[0]

    result = runner.invoke(app, [*common, "metadata", paper.id])

    assert result.exit_code == 0
    assert "Metadata resolved" in result.stdout
    resolved = list_papers(data_dir / "passagen.db")[0]
    assert resolved.status.value == "metadata_resolved"
    assert resolved.title == "Local Metadata Title"
    assert resolved.doi == "10.1000/local"
    assert resolved.metadata_sources["title"] == "pdf"

    result = runner.invoke(app, [*common, "metadata", paper.id])
    assert result.exit_code == 0
    assert "already resolved" in result.stdout


def test_update_one_then_all_papers(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    write_metadata_pdf(source_dir / "first.pdf", "First Paper")
    write_metadata_pdf(source_dir / "second.pdf", "Second Paper")
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir)]
    result = runner.invoke(app, [*common, "scan", str(source_dir)])
    assert result.exit_code == 0
    papers = {paper.original_filename: paper for paper in list_papers(data_dir / "passagen.db")}

    result = runner.invoke(app, [*common, "update", papers["first.pdf"].id])

    assert result.exit_code == 0
    assert "updated: 1, skipped: 0, failed: 0" in result.stdout
    current = {paper.original_filename: paper for paper in list_papers(data_dir / "passagen.db")}
    assert current["first.pdf"].status.value == "metadata_resolved"
    assert current["second.pdf"].status.value == "discovered"

    result = runner.invoke(
        app,
        [*common, "update", papers["first.pdf"].id, "--refresh"],
    )

    assert result.exit_code == 0
    assert "updated: 1, skipped: 0, failed: 0" in result.stdout

    result = runner.invoke(app, [*common, "update"])

    assert result.exit_code == 0
    assert "updated: 1, skipped: 1, failed: 0" in result.stdout
    assert all(
        paper.status.value == "metadata_resolved" for paper in list_papers(data_dir / "passagen.db")
    )

    result = runner.invoke(app, [*common, "update", "--refresh"])

    assert result.exit_code == 0
    assert "updated: 2, skipped: 0, failed: 0" in result.stdout


def test_update_all_isolates_paper_failure(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    write_metadata_pdf(source_dir / "missing.pdf", "Missing Paper")
    write_metadata_pdf(source_dir / "valid.pdf", "Valid Paper")
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir)]
    result = runner.invoke(app, [*common, "scan", str(source_dir)])
    assert result.exit_code == 0
    papers = {paper.original_filename: paper for paper in list_papers(data_dir / "passagen.db")}
    missing = papers["missing.pdf"].managed_pdf_path
    assert missing is not None
    (data_dir / missing).unlink()

    result = runner.invoke(app, [*common, "update"])

    assert result.exit_code == 1
    assert papers["missing.pdf"].id in result.stdout
    assert "updated: 1, skipped: 0, failed: 1" in result.stdout
    current = {paper.original_filename: paper for paper in list_papers(data_dir / "passagen.db")}
    assert current["missing.pdf"].status.value == "discovered"
    assert current["valid.pdf"].status.value == "metadata_resolved"


def test_update_rejects_unknown_paper(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["--data-dir", str(data_dir), "update", "missing"])

    assert result.exit_code == 1
    assert "Paper not found" in result.stdout
