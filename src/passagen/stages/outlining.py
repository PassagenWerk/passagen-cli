from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from passagen.config import LlmSettings, OutliningSettings
from passagen.llm import LlmProvider, LlmProviderError, OpenAICompatibleProvider
from passagen.models import PaperStatus
from passagen.providers import ProviderHealthSnapshot, ProviderUnavailableError
from passagen.repository import (
    ArtifactRecord,
    PaperRecord,
    finish_processing_run,
    get_artifact,
    get_paper,
    record_llm_call,
    save_outline_artifacts,
    start_processing_run,
    update_paper_status,
)
from passagen.stages.progress import ProgressCallback, report_progress
from passagen.stages.summarization import SUMMARY_SCHEMA_VERSION, StructuredSummary

logger = logging.getLogger(__name__)
OUTLINE_SCHEMA_VERSION = "1"
OUTLINE_PROMPT_VERSION = "1"
SUMMARY_ARTIFACT_KIND = "summary_json"
OUTLINE_ARTIFACT_KIND = "outline_zh_md"
_CHINESE_CHARACTER = re.compile(r"[\u3400-\u9fff]")
_SECTIONS = (
    ("introduction", "Introduction"),
    ("background", "Background"),
    ("design", "Design"),
    ("implementation", "Implementation"),
    ("evaluation", "Evaluation"),
    ("related_work", "Related Work"),
)


class OutlineError(RuntimeError):
    pass


class ChineseOutline(BaseModel):
    model_config = ConfigDict(extra="forbid")

    introduction: list[str] = Field(default_factory=list)
    background: list[str] = Field(default_factory=list)
    design: list[str] = Field(default_factory=list)
    implementation: list[str] = Field(default_factory=list)
    evaluation: list[str] = Field(default_factory=list)
    related_work: list[str] = Field(default_factory=list)

    @field_validator("*")
    @classmethod
    def require_chinese_content(cls, items: list[str]) -> list[str]:
        for item in items:
            if not item.strip() or _CHINESE_CHARACTER.search(item) is None:
                raise ValueError("outline entries must be non-empty Chinese text")
        return items


@dataclass(frozen=True, slots=True)
class OutlineResult:
    paper: PaperRecord
    artifact: ArtifactRecord | None
    outline: ChineseOutline | None
    updated: bool


def outline_paper(
    database_path: Path,
    data_dir: Path,
    paper_id: str,
    settings: LlmSettings,
    outlining: OutliningSettings,
    *,
    provider_health: ProviderHealthSnapshot | None = None,
    force: bool = False,
    provider: LlmProvider | None = None,
    execution_log_dir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> OutlineResult:
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise OutlineError(f"Paper not found: {paper_id}")
    existing = get_artifact(database_path, paper_id, OUTLINE_ARTIFACT_KIND)
    if paper.status is PaperStatus.OUTLINED and not force:
        return OutlineResult(paper, existing, None, updated=False)
    summary_artifact = get_artifact(database_path, paper_id, SUMMARY_ARTIFACT_KIND)
    if summary_artifact is None:
        raise OutlineError(f"Paper must have a validated summary before outlining: {paper_id}")
    try:
        summary_content = (data_dir / summary_artifact.path).read_bytes()
        summary = StructuredSummary.model_validate_json(summary_content)
    except (OSError, ValidationError) as exc:
        raise OutlineError(f"Cannot load validated summary for outlining: {exc}") from exc
    if summary.schema_version != SUMMARY_SCHEMA_VERSION:
        raise OutlineError(
            f"Unsupported summary schema version for outlining: {summary.schema_version}"
        )
    if force and paper.status is not PaperStatus.SUMMARIZED:
        update_paper_status(database_path, paper_id, PaperStatus.SUMMARIZED)

    if provider_health is not None:
        try:
            provider_health.require("llm")
        except ProviderUnavailableError as exc:
            raise OutlineError(str(exc)) from exc
    try:
        llm = provider or OpenAICompatibleProvider(settings)
    except LlmProviderError as exc:
        raise OutlineError(str(exc)) from exc
    run_id = start_processing_run(database_path, paper_id, "outline")
    prompt = _outline_prompt(summary)
    diagnostic_path = (
        execution_log_dir / "external" / "llm" / paper_id / "outline.json"
        if execution_log_dir is not None
        else None
    )
    report_progress(progress, "Generating Chinese outline...")
    raw_response: str | None = None
    try:
        response = llm.generate(prompt, max_tokens=outlining.max_output_tokens)
        raw_response = response.content
        record_llm_call(
            database_path,
            run_id,
            provider=llm.provider_name,
            model=llm.model,
            prompt_version=OUTLINE_PROMPT_VERSION,
            schema_version=OUTLINE_SCHEMA_VERSION,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
        )
        _write_diagnostic(
            diagnostic_path,
            prompt,
            llm,
            outlining.max_output_tokens,
            response.content,
        )
        outline = ChineseOutline.model_validate(_decode_json(response.content))
        markdown_content = _render_markdown(summary.identity.title, outline).encode()
        summary_sha256 = hashlib.sha256(summary_content).hexdigest()
        source = {
            "outline_schema_version": OUTLINE_SCHEMA_VERSION,
            "prompt_version": OUTLINE_PROMPT_VERSION,
            "summary_schema_version": summary.schema_version,
            "summary_sha256": summary_sha256,
            "provider": llm.provider_name,
            "model": llm.model,
            "prompt": prompt,
            "summary": summary.model_dump(mode="json"),
        }
        source_content = (json.dumps(source, ensure_ascii=False, indent=2) + "\n").encode()
        markdown_path = Path("papers") / paper_id / "outline.zh.md"
        source_path = Path("papers") / paper_id / "outline.source.json"
        _atomic_write(data_dir / markdown_path, markdown_content)
        _atomic_write(data_dir / source_path, source_content)
        updated, artifact = save_outline_artifacts(
            database_path,
            paper_id,
            markdown_path,
            source_path,
            version=OUTLINE_SCHEMA_VERSION,
            markdown_sha256=hashlib.sha256(markdown_content).hexdigest(),
            markdown_size_bytes=len(markdown_content),
            source_sha256=hashlib.sha256(source_content).hexdigest(),
            source_size_bytes=len(source_content),
        )
    except (LlmProviderError, ValidationError, json.JSONDecodeError, OutlineError) as exc:
        if isinstance(exc, LlmProviderError):
            record_llm_call(
                database_path,
                run_id,
                provider=llm.provider_name,
                model=llm.model,
                prompt_version=OUTLINE_PROMPT_VERSION,
                schema_version=OUTLINE_SCHEMA_VERSION,
                input_tokens=None,
                output_tokens=None,
                error_message=str(exc),
            )
        finish_processing_run(database_path, run_id, error_message=str(exc))
        _write_diagnostic(
            diagnostic_path,
            prompt,
            llm,
            outlining.max_output_tokens,
            response=raw_response,
            error=str(exc),
        )
        logger.error("outline failed: paper_id=%s error=%s", paper_id, exc)
        raise OutlineError(f"Chinese outline generation failed: {exc}") from exc
    except KeyboardInterrupt:
        finish_processing_run(database_path, run_id, error_message="interrupted")
        raise
    finish_processing_run(database_path, run_id)
    report_progress(progress, "Chinese outline saved.")
    logger.info("outline finished: paper_id=%s artifact=%s", paper_id, markdown_path)
    return OutlineResult(updated, artifact, outline, updated=True)


def _outline_prompt(summary: StructuredSummary) -> str:
    return (
        "Generate a concise Chinese outline using only facts in the supplied validated summary. "
        "Return JSON only. Translate faithfully; do not add explanations, facts, or placeholders. "
        "Each value must be an array of Chinese bullet text. Use an empty array when the summary "
        "does not support a section. Map content into exactly these sections: introduction for the "
        "problem and motivation; background for prior-work limitations; design for the approach "
        "and "
        "system; implementation for implementation details; evaluation for results, datasets, and "
        "workloads; related_work for research connections. Schema: "
        f"{json.dumps(ChineseOutline.model_json_schema(), ensure_ascii=False)}\n\n"
        f"Validated summary:\n{summary.model_dump_json()}"
    )


def _decode_json(raw: str) -> dict[str, Any]:
    content = raw.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise json.JSONDecodeError("Expected JSON object", content, 0)
    return result


def _render_markdown(title: str, outline: ChineseOutline) -> str:
    lines = [f"# {title} 中文提纲"]
    values = outline.model_dump()
    for field, heading in _SECTIONS:
        items = values[field]
        if not items:
            continue
        lines.extend(("", f"## {heading}"))
        lines.extend(f"- {item.strip()}" for item in items)
    return "\n".join(lines) + "\n"


def _write_diagnostic(
    path: Path | None,
    prompt: str,
    provider: LlmProvider,
    max_tokens: int,
    response: str | None = None,
    error: str | None = None,
) -> None:
    if path is None:
        return
    document = {
        "label": "outline",
        "provider": provider.provider_name,
        "model": provider.model,
        "max_tokens": max_tokens,
        "prompt": prompt,
    }
    if response is not None:
        document["response"] = response
    if error is not None:
        document["error"] = error
    _atomic_write(path, (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode())


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="outline-", suffix=".tmp", dir=path.parent)
    temp_path = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as output:
            descriptor = -1
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temp_path, path)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)
