from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from passagen.config import LlmSettings, OutliningSettings
from passagen.db import connect_database, initialize_database
from passagen.llm import LlmResponse
from passagen.models import Paper, PaperStatus
from passagen.repository import get_artifact, register_pdf, save_summary_artifacts
from passagen.stages.outlining import OutlineError, outline_paper
from passagen.stages.summarization import StructuredSummary


class FakeProvider:
    provider_name = "fake"
    model = "outline-model"

    def __init__(self, response: str) -> None:
        self.response = response
        self.prompts: list[str] = []
        self.max_tokens: list[int] = []

    def generate(self, prompt: str, *, max_tokens: int) -> LlmResponse:
        self.prompts.append(prompt)
        self.max_tokens.append(max_tokens)
        return LlmResponse(self.response, input_tokens=20, output_tokens=10)


def setup_summarized_paper(tmp_path: Path) -> tuple[Path, Path, str]:
    data_dir = tmp_path / "data"
    database_path = data_dir / "passagen.db"
    initialize_database(database_path)
    paper = Paper(original_filename="paper.pdf", pdf_sha256="b" * 64, file_size_bytes=1)
    register_pdf(database_path, paper, Path("pdfs/bb/paper.pdf"))
    summary = StructuredSummary.model_validate(
        {
            "identity": {"title": "Test Paper", "authors": [], "tags": []},
            "problem": {"problem_statement": "A test problem", "motivation": "A test need"},
            "evaluation": {
                "key_results": [{"claim": "The method is faster", "evidence_pages": [4]}]
            },
        }
    )
    relative_path = Path("papers") / paper.id / "summary.json"
    yaml_path = Path("papers") / paper.id / "summary.yaml"
    content = (summary.model_dump_json(indent=2) + "\n").encode()
    target = data_dir / relative_path
    target.parent.mkdir(parents=True)
    target.write_bytes(content)
    (data_dir / yaml_path).write_bytes(b"identity: {}\n")
    save_summary_artifacts(
        database_path,
        paper.id,
        relative_path,
        yaml_path,
        version=summary.schema_version,
        json_sha256=hashlib.sha256(content).hexdigest(),
        json_size_bytes=len(content),
        yaml_sha256=hashlib.sha256(b"identity: {}\n").hexdigest(),
        yaml_size_bytes=len(b"identity: {}\n"),
    )
    return database_path, data_dir, paper.id


def valid_outline() -> str:
    return json.dumps(
        {
            "introduction": ["本文研究一个测试问题及其动机。"],
            "background": [],
            "design": [],
            "implementation": [],
            "evaluation": ["实验表明该方法更快，证据见第 4 页。"],
            "related_work": [],
        }
    )


def test_outline_saves_markdown_source_and_call_audit(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_summarized_paper(tmp_path)
    provider = FakeProvider(valid_outline())

    result = outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(max_output_tokens=1200),
        provider=provider,
        execution_log_dir=tmp_path / "logs" / "run",
    )

    assert result.updated is True
    assert result.paper.status is PaperStatus.OUTLINED
    assert provider.max_tokens == [1200]
    markdown = (data_dir / "papers" / paper_id / "outline.zh.md").read_text()
    assert "# Test Paper 中文提纲" in markdown
    assert "## Introduction" in markdown
    assert "## Evaluation" in markdown
    assert "## Background" not in markdown
    source = json.loads(
        (data_dir / "papers" / paper_id / "outline.source.json").read_text(encoding="utf-8")
    )
    assert source["model"] == "outline-model"
    assert source["prompt_version"] == "1"
    assert source["summary"]["identity"]["title"] == "Test Paper"
    assert get_artifact(database_path, paper_id, "outline_source_json") is not None
    diagnostic = json.loads(
        (tmp_path / "logs" / "run" / "external" / "llm" / paper_id / "outline.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["response"] == valid_outline()
    with connect_database(database_path) as connection:
        assert connection.execute("SELECT stage FROM processing_runs").fetchone()[0] == "outline"
        assert connection.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 1


def test_outline_skips_existing_artifact_without_llm_call(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_summarized_paper(tmp_path)
    outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(),
        provider=FakeProvider(valid_outline()),
    )
    provider = FakeProvider(valid_outline())

    result = outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(),
        provider=provider,
    )

    assert result.updated is False
    assert provider.prompts == []


def test_updated_summary_marks_existing_outline_stale(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_summarized_paper(tmp_path)
    outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(),
        provider=FakeProvider(valid_outline()),
    )
    summary_path = Path("papers") / paper_id / "summary.json"
    yaml_path = Path("papers") / paper_id / "summary.yaml"
    summary = StructuredSummary.model_validate(
        {"identity": {"title": "Updated Paper", "authors": [], "tags": []}}
    )
    content = (summary.model_dump_json(indent=2) + "\n").encode()
    (data_dir / summary_path).write_bytes(content)
    save_summary_artifacts(
        database_path,
        paper_id,
        summary_path,
        yaml_path,
        version=summary.schema_version,
        json_sha256=hashlib.sha256(content).hexdigest(),
        json_size_bytes=len(content),
        yaml_sha256="d" * 64,
        yaml_size_bytes=1,
    )
    provider = FakeProvider(valid_outline())

    result = outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(),
        provider=provider,
    )

    assert result.updated is True
    assert result.paper.status is PaperStatus.OUTLINED
    assert "Updated Paper" in provider.prompts[0]


def test_forced_outline_failure_stops_at_summarized(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_summarized_paper(tmp_path)
    outline_paper(
        database_path,
        data_dir,
        paper_id,
        LlmSettings(),
        OutliningSettings(),
        provider=FakeProvider(valid_outline()),
    )

    with pytest.raises(OutlineError):
        outline_paper(
            database_path,
            data_dir,
            paper_id,
            LlmSettings(),
            OutliningSettings(),
            force=True,
            provider=FakeProvider("invalid"),
        )

    with connect_database(database_path) as connection:
        assert connection.execute("SELECT status FROM papers").fetchone()[0] == "summarized"


def test_outline_rejects_non_chinese_or_unknown_content(tmp_path: Path) -> None:
    database_path, data_dir, paper_id = setup_summarized_paper(tmp_path)
    provider = FakeProvider(
        json.dumps(
            {
                "introduction": ["Only English"],
                "background": [],
                "design": [],
                "implementation": [],
                "evaluation": [],
                "related_work": [],
                "extra": ["额外事实"],
            }
        )
    )

    with pytest.raises(OutlineError, match="generation failed"):
        outline_paper(
            database_path,
            data_dir,
            paper_id,
            LlmSettings(),
            OutliningSettings(),
            provider=provider,
            execution_log_dir=tmp_path / "logs" / "run",
        )

    diagnostic = json.loads(
        (tmp_path / "logs" / "run" / "external" / "llm" / paper_id / "outline.json").read_text(
            encoding="utf-8"
        )
    )
    assert diagnostic["response"] == provider.response
    with connect_database(database_path) as connection:
        assert connection.execute("SELECT status FROM papers").fetchone()[0] == "summarized"
        assert connection.execute("SELECT status FROM processing_runs").fetchone()[0] == "failed"


def test_outline_requires_validated_summary(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    database_path = data_dir / "passagen.db"
    initialize_database(database_path)
    paper = Paper(original_filename="paper.pdf", pdf_sha256="c" * 64, file_size_bytes=1)
    register_pdf(database_path, paper, Path("pdfs/cc/paper.pdf"))

    with pytest.raises(OutlineError, match="validated summary"):
        outline_paper(
            database_path,
            data_dir,
            paper.id,
            LlmSettings(),
            OutliningSettings(),
            provider=FakeProvider(valid_outline()),
        )
