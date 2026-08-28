from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from passagen.config import LlmSettings, MetadataSettings, ParsingSettings
from passagen.db import connect_database, initialize_database
from passagen.llm import LlmResponse
from passagen.models import Paper, PaperStatus
from passagen.parsing import ParsedPaper, ParsedSection
from passagen.repository import register_pdf, save_parsed_artifact
from passagen.summarization import SummaryError, summarize_paper
from passagen.updating import update_papers


class FakeProvider:
    provider_name = "fake"
    model = "test-model"

    def __init__(self, responses: list[str]) -> None:
        self.responses = responses
        self.prompts: list[str] = []

    def generate(self, prompt: str) -> LlmResponse:
        self.prompts.append(prompt)
        return LlmResponse(self.responses.pop(0), input_tokens=10, output_tokens=5)


def setup_parsed_paper(tmp_path: Path) -> tuple[Path, Path, str]:
    data_dir = tmp_path / "data"
    database_path = data_dir / "passagen.db"
    initialize_database(database_path)
    paper = Paper(original_filename="paper.pdf", pdf_sha256="a" * 64, file_size_bytes=1)
    register_pdf(database_path, paper, Path("pdfs/aa/paper.pdf"))
    parsed = ParsedPaper(
        parser="test",
        sections=(ParsedSection(title="Introduction", text="A test paper.", pages=(1,)),),
    )
    relative_path = Path("papers") / paper.id / "extracted.json"
    content = (parsed.model_dump_json() + "\n").encode()
    target = data_dir / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    save_parsed_artifact(
        database_path,
        paper.id,
        relative_path,
        version=parsed.schema_version,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        status=PaperStatus.PARSED,
    )
    return database_path, data_dir, paper.id


def valid_summary(title: str = "Test Paper") -> str:
    return json.dumps({"identity": {"title": title, "authors": [], "tags": []}})


def test_summarize_saves_validated_json_yaml_and_call_audit(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    provider = FakeProvider(['{"facts": ["A test paper"]}', valid_summary()])

    result = summarize_paper(database_path, data_dir, paper_id, LlmSettings(), provider=provider)

    assert result.updated is True
    assert result.paper.status is PaperStatus.SUMMARIZED
    assert result.summary is not None
    assert result.summary.identity.title == "Test Paper"
    assert (data_dir / "papers" / paper_id / "summary.json").is_file()
    assert (data_dir / "papers" / paper_id / "summary.yaml").is_file()
    with connect_database(database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2
        assert connection.execute("SELECT status FROM processing_runs").fetchone()[0] == "completed"


def test_summarize_locally_removes_json_code_fence(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    provider = FakeProvider(['{"facts": []}', f"```json\n{valid_summary()}\n```"])

    result = summarize_paper(database_path, data_dir, paper_id, LlmSettings(), provider=provider)

    assert result.updated is True
    assert len(provider.prompts) == 2


def test_summarize_uses_llm_repair_for_schema_error(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    provider = FakeProvider(['{"facts": []}', '{"identity": {"title": 1}}', valid_summary()])

    result = summarize_paper(database_path, data_dir, paper_id, LlmSettings(), provider=provider)

    assert result.updated is True
    assert len(provider.prompts) == 3
    assert (data_dir / "papers" / paper_id / "summary" / "raw" / "summary-repair-1.json").is_file()


def test_summarize_keeps_raw_response_when_repair_fails(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    provider = FakeProvider(['{"facts": []}', "not json", "still not json", "also not json"])

    with pytest.raises(SummaryError, match="schema validation"):
        summarize_paper(database_path, data_dir, paper_id, LlmSettings(), provider=provider)

    raw_dir = data_dir / "papers" / paper_id / "summary" / "raw"
    assert (raw_dir / "summary.json").read_text(encoding="utf-8") == "not json"
    assert (raw_dir / "summary-repair-2.json").is_file()
    with connect_database(database_path) as connection:
        assert connection.execute("SELECT status FROM processing_runs").fetchone()[0] == "failed"
        assert connection.execute("SELECT status FROM papers").fetchone()[0] == "failed"


def test_summarize_reuses_successful_section_facts_when_forced(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    summarize_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        provider=FakeProvider(['{"facts": ["A test paper"]}', valid_summary()]),
    )
    provider = FakeProvider([valid_summary("Rebuilt")])

    result = summarize_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        force=True,
        provider=provider,
    )

    assert result.summary is not None
    assert result.summary.identity.title == "Rebuilt"
    assert len(provider.prompts) == 1


def test_update_advances_parsed_paper_to_summary_when_llm_is_enabled(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_parsed_paper(tmp_path)
    provider = FakeProvider(['{"facts": []}', valid_summary()])

    result = update_papers(
        database_path,
        data_dir,
        MetadataSettings(),
        ParsingSettings(),
        llm_settings=LlmSettings(),
        summary_provider=provider,
    )

    assert result.target_status is PaperStatus.SUMMARIZED
    assert [paper.id for paper in result.updated] == [paper_id]
    assert result.updated[0].status is PaperStatus.SUMMARIZED
    assert len(provider.prompts) == 2
