import json
from pathlib import Path

import pytest
from passagen.assistant import ProviderCallError, SourceStatus
from passagen.research import CollectionSynthesis, CollectionSynthesisResult
from typer.testing import CliRunner

from passagen_cli import app

runner = CliRunner()


def _result(*, partial: bool = False) -> CollectionSynthesisResult:
    synthesis = CollectionSynthesis.model_validate(
        {
            "overview": "Two systems are compared.",
            "themes": [
                {
                    "name": "Systems",
                    "description": "Both papers study systems.",
                    "paper_ids": ["paper-1"],
                    "citation_ids": ["citation-1"],
                }
            ],
            "comparison_matrix": {
                "dimensions": ["Method"],
                "rows": [
                    {
                        "paper_id": "paper-1",
                        "cells": [
                            {
                                "dimension": "Method",
                                "value": "Evaluation",
                                "citation_ids": ["citation-1"],
                            }
                        ],
                    }
                ],
            },
            "claims": [{"text": "The paper evaluates a system.", "citation_ids": ["citation-1"]}],
            "citations": [
                {
                    "citation_id": "citation-1",
                    "paper_id": "paper-1",
                    "artifact_kind": "summary_json",
                    "artifact_id": "artifact-1",
                    "artifact_sha256": "1" * 64,
                    "summary_path": "problem.context",
                }
            ],
            "coverage": {
                "included_paper_ids": ["paper-1"],
                "missing_summary_paper_ids": ["paper-2"] if partial else [],
                "partial": partial,
            },
        }
    )
    return CollectionSynthesisResult(
        synthesis=synthesis,
        run_id="run-1",
        artifacts=[],
        source_status=SourceStatus(),
        disposition="generated",
        strategy="direct",
    )


class _FakeService:
    def __init__(
        self,
        result: CollectionSynthesisResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, bool, bool]] = []

    def synthesize(
        self, collection_id: str, *, allow_partial: bool = False, force: bool = False
    ) -> CollectionSynthesisResult:
        self.calls.append((collection_id, allow_partial, force))
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _invoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service: _FakeService,
    arguments: list[str],
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "passagen_cli.commands.organization._synthesis_service", lambda _ctx: service
    )
    return runner.invoke(app, ["--data-dir", str(tmp_path / "data"), *arguments])


def test_collection_synthesize_renders_core_json_and_forwards_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeService(_result(partial=True))

    result = _invoke(
        tmp_path,
        monkeypatch,
        service,
        [
            "collection",
            "synthesize",
            "collection-1",
            "--format",
            "json",
            "--allow-partial",
            "--force",
        ],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout)["overview"] == "Two systems are compared."
    assert service.calls == [("collection-1", True, True)]
    assert "generated, strategy=direct, run=run-1" in result.stderr
    assert "Partial coverage; missing summaries: paper-2" in result.stderr


def test_collection_compare_json_contains_only_the_core_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeService(_result())

    result = _invoke(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "compare", "collection-1", "--format", "json"],
    )

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "dimensions": ["Method"],
        "rows": [
            {
                "paper_id": "paper-1",
                "cells": [
                    {
                        "dimension": "Method",
                        "value": "Evaluation",
                        "citation_ids": ["citation-1"],
                    }
                ],
            }
        ],
    }
    assert service.calls == [("collection-1", False, False)]
    assert "Collection comparison:" in result.stderr


def test_collection_compare_markdown_uses_core_full_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(
        tmp_path,
        monkeypatch,
        _FakeService(_result()),
        ["collection", "compare", "collection-1"],
    )

    assert result.exit_code == 0
    assert result.stdout.startswith("# Collection Synthesis\n")
    assert "## Comparison" in result.stdout


def test_collection_synthesis_error_is_concise_and_only_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = _invoke(
        tmp_path,
        monkeypatch,
        _FakeService(error=ProviderCallError("provider unavailable")),
        ["collection", "synthesize", "collection-1", "--format", "json"],
    )

    assert result.exit_code == 1
    assert result.stdout == ""
    assert result.stderr.strip() == "Collection synthesis error: provider unavailable"


def test_collection_synthesis_rejects_unknown_format(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _FakeService(_result())

    result = _invoke(
        tmp_path,
        monkeypatch,
        service,
        ["collection", "synthesize", "collection-1", "--format", "yaml"],
    )

    assert result.exit_code == 2
    assert service.calls == []
