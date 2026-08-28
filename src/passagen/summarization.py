from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from passagen.config import LlmSettings, SummarizationSettings
from passagen.llm import LlmProvider, LlmProviderError, LlmResponse, OpenAICompatibleProvider
from passagen.models import PaperStatus
from passagen.parsing import ParsedPaper, ParsedSection
from passagen.progress import ProgressCallback, report_progress
from passagen.repository import (
    ArtifactRecord,
    PaperRecord,
    finish_processing_run,
    get_artifact,
    get_paper,
    record_llm_call,
    save_summary_artifacts,
    start_processing_run,
    update_paper_status,
)

logger = logging.getLogger(__name__)
SUMMARY_SCHEMA_VERSION = "1"
SUMMARY_PROMPT_VERSION = "1"
EXTRACTED_ARTIFACT_KIND = "extracted_json"
SUMMARY_ARTIFACT_KIND = "summary_json"


class SummaryError(RuntimeError):
    pass


class SummaryIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    authors: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


class SummaryProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    problem_statement: str | None = None
    motivation: str | None = None
    limitations_of_prior_work: list[str] = Field(default_factory=list)


class SummaryApproach(BaseModel):
    model_config = ConfigDict(extra="forbid")

    core_idea: str | None = None
    architecture: str | None = None
    algorithm: str | None = None
    novelty: list[str] = Field(default_factory=list)


class SummarySystem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hardware: list[str] = Field(default_factory=list)
    software: list[str] = Field(default_factory=list)


class SummaryImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools_and_dependencies: list[str] = Field(default_factory=list)


class SummaryKeyResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim: str
    value: str | None = None
    baseline: str | None = None
    evidence_pages: list[int] = Field(min_length=1)


class SummaryEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key_results: list[SummaryKeyResult] = Field(default_factory=list)
    datasets: list[str] = Field(default_factory=list)
    workloads: list[str] = Field(default_factory=list)


class SummaryConclusion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limitations: list[str] = Field(default_factory=list)
    useful_conclusions: list[str] = Field(default_factory=list)
    reusable_evaluation_methods: list[str] = Field(default_factory=list)


class SummaryRelatedWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    relationship: str | None = None


class SummaryResearchConnections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    related_works: list[SummaryRelatedWork] = Field(default_factory=list)


class StructuredSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = SUMMARY_SCHEMA_VERSION
    identity: SummaryIdentity
    problem: SummaryProblem = Field(default_factory=SummaryProblem)
    approach: SummaryApproach = Field(default_factory=SummaryApproach)
    system: SummarySystem = Field(default_factory=SummarySystem)
    implementation: SummaryImplementation = Field(default_factory=SummaryImplementation)
    evaluation: SummaryEvaluation = Field(default_factory=SummaryEvaluation)
    conclusion: SummaryConclusion = Field(default_factory=SummaryConclusion)
    research_connections: SummaryResearchConnections = Field(
        default_factory=SummaryResearchConnections
    )


@dataclass(frozen=True, slots=True)
class SummaryResult:
    paper: PaperRecord
    artifact: ArtifactRecord | None
    summary: StructuredSummary | None
    updated: bool


def summarize_paper(
    database_path: Path,
    data_dir: Path,
    paper_id: str,
    settings: LlmSettings,
    summarization: SummarizationSettings,
    *,
    force: bool = False,
    provider: LlmProvider | None = None,
    execution_log_dir: Path | None = None,
    progress: ProgressCallback | None = None,
) -> SummaryResult:
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise SummaryError(f"Paper not found: {paper_id}")
    existing = get_artifact(database_path, paper_id, SUMMARY_ARTIFACT_KIND)
    if (
        paper.status in {PaperStatus.SUMMARIZED, PaperStatus.OUTLINED, PaperStatus.COMPLETED}
        and not force
    ):
        return SummaryResult(paper, existing, None, updated=False)
    extracted = get_artifact(database_path, paper_id, EXTRACTED_ARTIFACT_KIND)
    if extracted is None:
        raise SummaryError(f"Paper must be parsed before summarization: {paper_id}")
    try:
        parsed = ParsedPaper.model_validate_json(
            (data_dir / extracted.path).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise SummaryError(f"Cannot load parsed paper for summarization: {exc}") from exc

    llm = provider or OpenAICompatibleProvider(settings)
    run_id = start_processing_run(database_path, paper_id, "summarize")
    facts_dir = data_dir / "papers" / paper_id / "summary" / "facts"
    call_log_dir = (
        execution_log_dir / "external" / "llm" / paper_id if execution_log_dir is not None else None
    )
    try:
        facts = _section_facts(
            parsed,
            facts_dir,
            summarization.max_chunk_characters,
            summarization.fact_max_output_tokens,
            call_log_dir,
            llm,
            database_path,
            run_id,
            progress,
        )
        report_progress(progress, "Generating structured summary...")
        raw_response = _generate(
            llm,
            _summary_prompt(paper, facts),
            database_path,
            run_id,
            call_log_dir / "summary.json" if call_log_dir is not None else None,
            "summary",
            summarization.summary_max_output_tokens,
        )
        summary = _validate_or_repair(
            raw_response.content,
            llm,
            database_path,
            run_id,
            call_log_dir,
            summarization.summary_max_output_tokens,
            progress,
        )
        json_path = Path("papers") / paper_id / "summary.json"
        yaml_path = Path("papers") / paper_id / "summary.yaml"
        json_content = (summary.model_dump_json(indent=2) + "\n").encode()
        yaml_content = yaml.safe_dump(
            summary.model_dump(mode="json"), sort_keys=False, allow_unicode=True
        ).encode()
        _atomic_write(data_dir / json_path, json_content)
        _atomic_write(data_dir / yaml_path, yaml_content)
        updated, artifact = save_summary_artifacts(
            database_path,
            paper_id,
            json_path,
            yaml_path,
            version=SUMMARY_SCHEMA_VERSION,
            json_sha256=hashlib.sha256(json_content).hexdigest(),
            json_size_bytes=len(json_content),
            yaml_sha256=hashlib.sha256(yaml_content).hexdigest(),
            yaml_size_bytes=len(yaml_content),
        )
    except (LlmProviderError, SummaryError) as exc:
        finish_processing_run(database_path, run_id, error_message=str(exc))
        update_paper_status(database_path, paper_id, PaperStatus.FAILED)
        logger.error("summary failed: paper_id=%s error=%s", paper_id, exc)
        raise SummaryError(str(exc)) from exc
    finish_processing_run(database_path, run_id)
    report_progress(progress, "Structured summary saved.")
    logger.info("summary finished: paper_id=%s artifact=%s", paper_id, json_path)
    return SummaryResult(updated, artifact, summary, updated=True)


def _section_facts(
    parsed: ParsedPaper,
    facts_dir: Path,
    max_chunk_characters: int,
    max_output_tokens: int,
    call_log_dir: Path | None,
    provider: LlmProvider,
    database_path: Path,
    run_id: str,
    progress: ProgressCallback | None,
) -> list[str]:
    chunks = _chunks(parsed.sections, max_chunk_characters)
    facts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        digest = hashlib.sha256(f"{SUMMARY_PROMPT_VERSION}\0{chunk}".encode()).hexdigest()
        path = facts_dir / f"section-{digest}.json"
        if path.exists():
            try:
                facts.append(str(json.loads(path.read_text(encoding="utf-8"))["response"]))
                report_progress(progress, f"Reusing section facts {index}/{len(chunks)}.")
                continue
            except (OSError, KeyError, TypeError, ValueError):
                pass
        report_progress(progress, f"Summarizing section facts {index}/{len(chunks)}...")
        response = _generate(
            provider,
            _facts_prompt(chunk),
            database_path,
            run_id,
            call_log_dir / f"facts-{index:03d}.json" if call_log_dir is not None else None,
            f"facts {index}/{len(chunks)}",
            max_output_tokens,
        )
        _atomic_write(
            path,
            json.dumps(
                {"prompt_version": SUMMARY_PROMPT_VERSION, "response": response.content}
            ).encode(),
        )
        facts.append(response.content)
    return facts


def _generate(
    provider: LlmProvider,
    prompt: str,
    database_path: Path,
    run_id: str,
    diagnostic_path: Path | None,
    label: str,
    max_tokens: int,
) -> LlmResponse:
    diagnostic = {
        "label": label,
        "provider": provider.provider_name,
        "model": provider.model,
        "max_tokens": max_tokens,
        "prompt": prompt,
    }
    if diagnostic_path is not None:
        _atomic_write(
            diagnostic_path, json.dumps(diagnostic, ensure_ascii=False, indent=2).encode()
        )
    logger.info(
        "llm request: label=%s model=%s prompt_chars=%s max_tokens=%s diagnostic=%s",
        label,
        provider.model,
        len(prompt),
        max_tokens,
        diagnostic_path or "not_saved",
    )
    logger.debug("llm request content: label=%s\n%s", label, prompt)
    try:
        response = provider.generate(prompt, max_tokens=max_tokens)
    except LlmProviderError as exc:
        record_llm_call(
            database_path,
            run_id,
            provider=provider.provider_name,
            model=provider.model,
            prompt_version=SUMMARY_PROMPT_VERSION,
            schema_version=SUMMARY_SCHEMA_VERSION,
            input_tokens=None,
            output_tokens=None,
            error_message=str(exc),
        )
        diagnostic["error"] = str(exc)
        if diagnostic_path is not None:
            _atomic_write(
                diagnostic_path, json.dumps(diagnostic, ensure_ascii=False, indent=2).encode()
            )
        raise
    record_llm_call(
        database_path,
        run_id,
        provider=provider.provider_name,
        model=provider.model,
        prompt_version=SUMMARY_PROMPT_VERSION,
        schema_version=SUMMARY_SCHEMA_VERSION,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
    )
    diagnostic.update(
        {
            "response": response.content,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "reasoning_tokens": response.reasoning_tokens,
            "finish_reason": response.finish_reason,
        }
    )
    if diagnostic_path is not None:
        _atomic_write(
            diagnostic_path, json.dumps(diagnostic, ensure_ascii=False, indent=2).encode()
        )
    logger.info(
        "llm response: label=%s response_chars=%s input_tokens=%s output_tokens=%s "
        "reasoning_tokens=%s finish_reason=%s diagnostic=%s",
        label,
        len(response.content),
        response.input_tokens,
        response.output_tokens,
        response.reasoning_tokens,
        response.finish_reason,
        diagnostic_path or "not_saved",
    )
    logger.debug("llm response content: label=%s\n%s", label, response.content)
    return response


def _validate_or_repair(
    raw: str,
    provider: LlmProvider,
    database_path: Path,
    run_id: str,
    call_log_dir: Path | None,
    max_output_tokens: int,
    progress: ProgressCallback | None,
) -> StructuredSummary:
    current = raw
    for attempt in range(3):
        try:
            return StructuredSummary.model_validate(_decode_json(current))
        except (json.JSONDecodeError, ValidationError) as exc:
            if attempt == 2:
                raise SummaryError(f"Summary failed schema validation: {exc}") from exc
            report_progress(progress, f"Repairing invalid summary ({attempt + 1}/2)...")
            current = _generate(
                provider,
                _repair_prompt(current, str(exc)),
                database_path,
                run_id,
                call_log_dir / f"repair-{attempt + 1}.json" if call_log_dir is not None else None,
                f"repair {attempt + 1}/2",
                max_output_tokens,
            ).content
    raise AssertionError("unreachable")


def _decode_json(raw: str) -> dict[str, Any]:
    content = raw.strip()
    if content.startswith("```") and content.endswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    result = json.loads(content)
    if not isinstance(result, dict):
        raise json.JSONDecodeError("Expected JSON object", content, 0)
    return result


def _chunks(sections: tuple[ParsedSection, ...], limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for section in sections:
        text = (
            f"Section: {section.title or 'Untitled'}\n"
            f"Pages: {list(section.pages)}\n{section.text}\n"
        )
        while text:
            available = limit - len(current)
            if available <= 0:
                chunks.append(current)
                current = ""
                available = limit
            current += text[:available]
            text = text[available:]
    if current:
        chunks.append(current)
    if not chunks:
        raise SummaryError("Parsed paper has no text sections")
    return chunks


def _facts_prompt(chunk: str) -> str:
    return (
        "Extract only the facts needed to build a structured paper summary. "
        "Do not restate every sentence or infer missing facts. Deduplicate related facts. "
        "Return JSON with one `facts` array containing at most 30 concise English strings; "
        "each string must be at most 25 words and include source page numbers when available.\n\n"
        + chunk
    )


def _summary_prompt(paper: PaperRecord, facts: list[str]) -> str:
    return (
        "Create an English structured paper summary from the section facts below. "
        "Return JSON only. Do not invent facts. Use null or empty lists when unsupported. "
        "Every evaluation.key_results item must include evidence_pages from the supplied facts. "
        f"The paper identity is title={paper.title!r}, authors={list(paper.authors)!r}, "
        f"year={paper.year!r}, venue={paper.venue!r}, doi={paper.doi!r}, "
        f"arxiv_id={paper.arxiv_id!r}. "
        "The JSON must validate against this schema: "
        f"{json.dumps(StructuredSummary.model_json_schema())}\n\n"
        f"Section facts:\n{json.dumps(facts)}"
    )


def _repair_prompt(raw: str, error: str) -> str:
    return (
        "Repair the following candidate into JSON that validates against the supplied schema. "
        "Return JSON only. Preserve facts; do not add unsupported information. "
        f"Schema: {json.dumps(StructuredSummary.model_json_schema())}\n"
        f"Validation error: {error}\nCandidate: {raw}"
    )


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="summary-", suffix=".tmp", dir=path.parent)
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
