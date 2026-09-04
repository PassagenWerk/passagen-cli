import importlib
import re
from importlib.metadata import version
from pathlib import Path

import pymupdf
import pytest
from typer.testing import CliRunner

from passagen.catalog import CatalogService
from passagen.cli import app
from passagen.providers import LlmResponse, ProviderHealthSnapshot, ProviderStatus
from passagen.storage.database import SCHEMA_VERSION, connect_database
from passagen.storage.repository import list_papers

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolate_cli_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "passagen.stages.summarization.service.OpenAICompatibleProvider",
        lambda _settings: _FakeLlmProvider(),
    )
    monkeypatch.setattr(
        "passagen.stages.outlining.service.OpenAICompatibleProvider",
        lambda _settings: _FakeLlmProvider(),
    )
    monkeypatch.setattr(
        importlib.import_module("passagen.cli.app"),
        "check_provider_health",
        lambda _settings: ProviderHealthSnapshot(
            {
                name: ProviderStatus(name, True, "test")
                for name in ("crossref", "arxiv", "grobid", "llm")
            }
        ),
    )


class _FakeLlmProvider:
    provider_name = "fake"
    model = "test-model"
    calls = 0

    def generate(self, prompt: str, *, max_tokens: int) -> LlmResponse:
        del max_tokens
        type(self).calls += 1
        if "Extract evidence-backed facts" in prompt:
            return LlmResponse('{"facts": []}', input_tokens=10, output_tokens=5)
        if "Create a detailed English technical-paper outline" in prompt:
            return LlmResponse(
                '{"introduction":{"thesis":"The paper introduces a test problem.","points":[]}}',
                input_tokens=10,
                output_tokens=5,
            )
        return LlmResponse(
            '{"identity": {"title": "Test", "authors": []}}',
            input_tokens=10,
            output_tokens=5,
        )


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
providers:
  crossref:
    enabled: false
  arxiv:
    enabled: false
pipeline:
  parsing:
    parser: pymupdf
"""
    )


def latest_execution_log(root: Path) -> str:
    log_paths = sorted((root / "logs").glob("*/log.txt"))
    assert log_paths
    return log_paths[-1].read_text(encoding="utf-8")


def test_help() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "Manage paper PDFs" in result.stdout
    assert "Import PDFs from a directory" in result.stdout
    assert "Resume one or all papers" in result.stdout


def test_version_uses_package_metadata() -> None:
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == version("passagen")


def _health_snapshot(*available: str, unavailable: str) -> ProviderHealthSnapshot:
    statuses = {
        name: ProviderStatus(name, True, "HTTP 200")
        for name in ("arxiv", "crossref", "grobid", "llm")
        if name in available
    }
    if unavailable:
        statuses[unavailable] = ProviderStatus(unavailable, False, "connection refused")
    return ProviderHealthSnapshot(statuses)


def test_check_reports_reachable_providers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    monkeypatch.setattr(
        "passagen.cli.commands.health.check_provider_health",
        lambda _settings: _health_snapshot("arxiv", "crossref", "grobid", "llm", unavailable=""),
    )

    result = runner.invoke(app, ["--config", str(config_path), "check"])

    assert result.exit_code == 0
    for name in ("arxiv", "crossref", "grobid", "llm"):
        assert name in result.output
    assert "unavailable" not in result.output


def test_check_fails_when_a_provider_is_unreachable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    monkeypatch.setattr(
        "passagen.cli.commands.health.check_provider_health",
        lambda _settings: _health_snapshot("arxiv", "crossref", "llm", unavailable="grobid"),
    )

    result = runner.invoke(app, ["--config", str(config_path), "check"])

    assert result.exit_code == 1
    assert "grobid" in result.output
    assert "unavailable" in result.output
    assert "connection refused" in result.output


@pytest.mark.parametrize(
    ("arguments", "description"),
    [
        (["config", "check", "--help"], "Validate configuration and prompt templates"),
        (["db", "init", "--help"], "Initialize the SQLite database"),
        (["db", "status", "--help"], "Show the current database schema version"),
        (["db", "backup", "--help"], "Create a consistent SQLite backup"),
        (["logs", "clean", "--help"], "Move historical execution logs"),
        (["artifacts", "check", "--help"], "Verify paths, file sizes"),
        (["scan", "--help"], "Import PDFs from a directory"),
        (["run", "--help"], "advance all pending papers"),
        (["list", "--help"], "List papers"),
        (["metadata", "--help"], "Extract local PDF metadata"),
        (["update", "--help"], "last successful stage"),
        (["parse", "--help"], "Parse full text into extracted.json"),
        (["summarize", "--help"], "Structured Summary v2"),
        (["outline", "--help"], "hierarchical English technical outline"),
        (["show", "--help"], "Show paper metadata"),
    ],
)
def test_command_help_includes_description(arguments: list[str], description: str) -> None:
    result = runner.invoke(app, arguments)

    assert result.exit_code == 0
    assert description in result.stdout


def test_database_init_uses_current_directory_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, ["db", "init"])

    assert result.exit_code == 0
    assert (tmp_path / "data" / "passagen.db").exists()


def test_config_check_rejects_invalid_prompt_template(tmp_path: Path) -> None:
    prompt = tmp_path / "facts.prompt"
    prompt.write_text("Unknown placeholder: $unknown\n")
    config = tmp_path / "passagen.yaml"
    config.write_text(f"pipeline:\n  summarization:\n    facts_prompt_path: {prompt}\n")

    result = runner.invoke(app, ["--config", str(config), "config", "check"])

    assert result.exit_code == 2
    assert "Prompt configuration error" in result.stdout
    assert "unknown placeholders" in result.stdout


def test_database_init_and_status(tmp_path: Path) -> None:
    data_dir = tmp_path / "new" / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0
    assert (data_dir / "passagen.db").exists()

    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "status"])
    assert result.exit_code == 0
    assert f"schema version: {SCHEMA_VERSION}" in result.stdout


def test_database_backup_and_artifact_check_commands(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"]).exit_code == 0
    backup = tmp_path / "backup.db"

    result = runner.invoke(
        app,
        ["--data-dir", str(data_dir), "db", "backup", str(backup)],
    )

    assert result.exit_code == 0
    assert backup.is_file()
    result = runner.invoke(app, ["--data-dir", str(data_dir), "artifacts", "check"])
    assert result.exit_code == 0
    assert "Checked: 0; invalid: 0" in result.stdout


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


def test_collection_and_tag_commands_manage_paper_organization(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    assert runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"]).exit_code == 0
    with connect_database(data_dir / "passagen.db") as connection:
        connection.execute(
            """
            INSERT INTO papers (id, original_filename, pdf_sha256, status)
            VALUES (?, ?, ?, ?)
            """,
            ("paper-1", "paper.pdf", "1" * 64, "discovered"),
        )
    common = ["--data-dir", str(data_dir)]

    assert runner.invoke(app, [*common, "collection", "create", "Reading"]).exit_code == 0
    assert runner.invoke(app, [*common, "tag", "create", "Important"]).exit_code == 0
    catalog = CatalogService(data_dir / "passagen.db", data_dir)
    collection = catalog.list_collections()[0]
    tag = catalog.list_tags()[0]
    catalog.update_collection(collection.id, description="Keep this")
    catalog.update_tag(tag.id, color="#123456")

    collection_list = runner.invoke(app, [*common, "collection", "list"])
    tag_list = runner.invoke(app, [*common, "tag", "list"])
    assert collection.id in collection_list.stdout
    assert tag.id in tag_list.stdout

    assert (
        runner.invoke(app, [*common, "collection", "add", collection.id, "paper-1"]).exit_code == 0
    )
    assert (
        runner.invoke(app, [*common, "collection", "add", collection.id, "paper-1"]).exit_code == 0
    )
    assert runner.invoke(app, [*common, "tag", "add", tag.id, "paper-1"]).exit_code == 0
    assert runner.invoke(app, [*common, "tag", "add", tag.id, "paper-1"]).exit_code == 0

    assert (
        runner.invoke(
            app, [*common, "collection", "rename", collection.id, "Core reading"]
        ).exit_code
        == 0
    )
    assert runner.invoke(app, [*common, "tag", "rename", tag.id, "Must read"]).exit_code == 0
    assert catalog.get_collection(collection.id).description == "Keep this"
    assert catalog.get_tag(tag.id).color == "#123456"

    assert (
        runner.invoke(app, [*common, "collection", "remove", collection.id, "paper-1"]).exit_code
        == 0
    )
    assert runner.invoke(app, [*common, "tag", "remove", tag.id, "paper-1"]).exit_code == 0
    missing = runner.invoke(app, [*common, "tag", "remove", tag.id, "paper-1"])
    assert missing.exit_code == 1
    assert "does not have the tag" in missing.stdout

    assert runner.invoke(app, [*common, "collection", "delete", collection.id]).exit_code == 0
    assert runner.invoke(app, [*common, "tag", "delete", tag.id]).exit_code == 0
    assert catalog.get_paper("paper-1").id == "paper-1"


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


@pytest.mark.slow
def test_parse_command_writes_extracted_artifact(tmp_path: Path) -> None:
    source_dir = tmp_path / "inbox"
    pdf_path = source_dir / "paper.pdf"
    write_metadata_pdf(
        pdf_path,
        "Parsed Paper",
        "1 Introduction\nThis paper contains enough text for full text parsing.",
    )
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir)]
    assert runner.invoke(app, [*common, "scan", str(source_dir)]).exit_code == 0
    paper = list_papers(data_dir / "passagen.db")[0]
    assert runner.invoke(app, [*common, "metadata", paper.id]).exit_code == 0

    result = runner.invoke(app, [*common, "parse", paper.id, "--parser", "pymupdf"])

    assert result.exit_code == 0
    assert "Parsed" in result.stdout
    assert "with pymupdf" in result.stdout
    current = list_papers(data_dir / "passagen.db")[0]
    assert current.status.value == "parsed"
    assert (data_dir / "papers" / paper.id / "extracted.json").is_file()

    result = runner.invoke(app, [*common, "parse", paper.id])
    assert result.exit_code == 0
    assert "already parsed" in result.stdout


@pytest.mark.slow
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
    assert "Paper 1/1 [stage 1/4: metadata]" in result.stdout
    assert "Paper 1/1 [stage 2/4: full text]" in result.stdout
    assert "Paper 1/1 [stage 3/4: summary]" in result.stdout
    assert "Paper 1/1 [stage 4/4: outline]" in result.stdout
    assert "updated: 1, skipped: 0, failed: 0" in result.stdout
    update_log = latest_execution_log(tmp_path)
    assert "update stage started:" in update_log
    assert "stage=metadata" in update_log
    assert "stage=full_text" in update_log
    current = {paper.original_filename: paper for paper in list_papers(data_dir / "passagen.db")}
    assert current["first.pdf"].status.value == "outlined"
    assert current["second.pdf"].status.value == "discovered"

    result = runner.invoke(
        app,
        [*common, "update", papers["first.pdf"].id, "--force"],
    )

    assert result.exit_code == 0
    assert "updated: 1, skipped: 0, failed: 0" in result.stdout

    result = runner.invoke(app, [*common, "update"])

    assert result.exit_code == 0
    assert "updated: 1, skipped: 1, failed: 0" in result.stdout
    assert all(paper.status.value == "outlined" for paper in list_papers(data_dir / "passagen.db"))

    result = runner.invoke(app, [*common, "update", "--force"])

    assert result.exit_code == 0
    assert "updated: 2, skipped: 0, failed: 0" in result.stdout


@pytest.mark.slow
def test_run_is_idempotent_and_does_not_repeat_llm_calls(tmp_path: Path) -> None:
    _FakeLlmProvider.calls = 0
    source_dir = tmp_path / "inbox"
    write_metadata_pdf(
        source_dir / "paper.pdf",
        "Pipeline Paper",
        "1 Introduction\nThis paper has enough text for the complete pipeline.",
    )
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir), "run", str(source_dir)]

    first = runner.invoke(app, common)

    assert first.exit_code == 0
    assert "Imported: 1" in first.stdout
    assert "LLM usage" in first.stdout
    assert re.search(r"total\D+3\D+45\D+30\D+15", first.stdout)
    assert re.search(r"fact\D+1\D+15\D+10\D+5", first.stdout)
    assert re.search(r"summary\D+1\D+15\D+10\D+5", first.stdout)
    assert re.search(r"outline\D+1\D+15\D+10\D+5", first.stdout)
    assert list_papers(data_dir / "passagen.db")[0].status.value == "outlined"
    first_call_count = _FakeLlmProvider.calls
    assert first_call_count == 3

    second = runner.invoke(app, common)

    assert second.exit_code == 0
    assert "Imported: 0, skipped: 1" in second.stdout
    assert "updated: 0, update skipped: 1" in second.stdout
    assert _FakeLlmProvider.calls == first_call_count


@pytest.mark.slow
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
    assert current["valid.pdf"].status.value == "outlined"


def test_update_rejects_unknown_paper(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    result = runner.invoke(app, ["--data-dir", str(data_dir), "db", "init"])
    assert result.exit_code == 0

    result = runner.invoke(app, ["--data-dir", str(data_dir), "update", "missing"])

    assert result.exit_code == 1
    assert "Paper not found" in result.stdout


def test_scan_and_metadata_write_detailed_execution_logs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    source_dir = tmp_path / "inbox"
    write_metadata_pdf(
        source_dir / "paper.pdf",
        "Logged Paper",
        "DOI: 10.1000/logged",
    )
    config_path = tmp_path / "passagen.yaml"
    write_offline_config(config_path)
    data_dir = tmp_path / "data"
    common = ["--config", str(config_path), "--data-dir", str(data_dir)]

    result = runner.invoke(app, [*common, "scan", str(source_dir)])

    assert result.exit_code == 0
    assert "Discovering PDFs" in result.stdout
    assert "Importing PDF 1/1: paper.pdf" in result.stdout
    assert "Scan complete: 1 imported, 0 skipped, 0 failed." in result.stdout
    scan_log = latest_execution_log(tmp_path)
    assert "execution started: command=scan" in scan_log
    assert f"scan candidate: file={source_dir / 'paper.pdf'}" in scan_log
    assert "scan imported:" in scan_log
    assert "scan finished: imported=1 skipped=0 failed=0" in scan_log
    paper = list_papers(data_dir / "passagen.db")[0]

    result = runner.invoke(app, [*common, "metadata", paper.id])

    assert result.exit_code == 0
    assert "Reading local PDF metadata: paper.pdf" in result.stdout
    assert "Saving resolved metadata" in result.stdout
    assert "Metadata saved." in result.stdout
    metadata_log = latest_execution_log(tmp_path)
    assert "execution started: command=metadata" in metadata_log
    assert f"metadata started: paper_id={paper.id}" in metadata_log
    assert "metadata local extraction succeeded:" in metadata_log
    assert "metadata route skipped: provider=Crossref reason=disabled" in metadata_log
    assert "metadata route skipped: provider=arXiv reason=disabled" in metadata_log
    assert "metadata finished:" in metadata_log
    assert len(list((tmp_path / "logs").glob("*/log.txt"))) == 2
