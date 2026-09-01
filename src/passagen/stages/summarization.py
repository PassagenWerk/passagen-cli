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
from passagen.external import LlmCallStats, LlmStage, TrackedLlmProvider
from passagen.llm import LlmProvider, LlmProviderError, LlmResponse, OpenAICompatibleProvider
from passagen.models import PaperStatus
from passagen.parsing import ParsedPaper, ParsedSection
from passagen.prompting import (
    PromptTemplate,
    PromptTemplateError,
    load_summary_prompt_templates,
)
from passagen.providers import ProviderHealthSnapshot, ProviderUnavailableError
from passagen.stages.progress import ProgressCallback, report_progress
from passagen.storage.repository import (
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
SUMMARY_SCHEMA_VERSION = "2"
SUMMARY_PROMPT_VERSION = "2"
EXTRACTED_ARTIFACT_KIND = "extracted_json"
SUMMARY_ARTIFACT_KIND = "summary_json"


class SummaryError(RuntimeError):
    pass


class ExtractedFacts(BaseModel):
    model_config = ConfigDict(extra="forbid")

    facts: list[str] = Field(
        default_factory=list,
        description=(
            "Evidence-backed English facts with source page numbers when known; preserve metric "
            "ownership, comparison direction, units, and conditions."
        ),
    )


class SummaryIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    authors: list[str] = Field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None


class PaperClassification(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_type: str | None = Field(
        default=None,
        description=(
            "General paper type, such as system, architecture, compiler, algorithm, "
            "measurement, or empirical study."
        ),
    )
    topics: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)


class SummaryProblem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    context: str | None = Field(
        default=None, description="Technical context needed to understand the problem."
    )
    problem_statement: str | None = Field(
        default=None, description="The specific research problem addressed by the paper."
    )
    motivation: str | None = Field(
        default=None, description="Why solving the stated problem matters."
    )
    goals: list[str] = Field(default_factory=list)
    non_goals: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    prior_work_limitations: list[str] = Field(
        default_factory=list,
        description="Concrete limitations of prior approaches stated by the paper.",
    )


class Contribution(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str | None = Field(
        default=None,
        description=(
            "Contribution category, such as design, mechanism, implementation, analysis, "
            "benchmark, measurement, or methodology."
        ),
    )
    statement: str
    evidence_pages: list[int] = Field(default_factory=list)


class DesignComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    role: str | None = None
    details: list[str] = Field(default_factory=list)
    interactions: list[str] = Field(default_factory=list)
    evidence_pages: list[int] = Field(default_factory=list)


class DesignProcess(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    steps: list[str] = Field(default_factory=list)
    evidence_pages: list[int] = Field(default_factory=list)


class SummaryDesign(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview: str | None = Field(
        default=None, description="High-level design and how it addresses the stated problem."
    )
    components: list[DesignComponent] = Field(default_factory=list)
    processes: list[DesignProcess] = Field(default_factory=list)
    key_mechanisms: list[str] = Field(default_factory=list)
    design_decisions: list[str] = Field(default_factory=list)
    tradeoffs: list[str] = Field(default_factory=list)


class SummaryImplementation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prototype_scope: str | None = None
    implemented_components: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    frameworks_and_dependencies: list[str] = Field(
        default_factory=list,
        description="Frameworks, libraries, toolchains, and external dependencies.",
    )
    hardware_platforms: list[str] = Field(default_factory=list)
    software_platforms: list[str] = Field(default_factory=list)
    code_size: str | None = None
    deployment_model: str | None = None
    engineering_details: list[str] = Field(default_factory=list)


class EvaluationEnvironment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hardware: list[str] = Field(default_factory=list)
    software: list[str] = Field(default_factory=list)
    topology_or_scale: str | None = None
    configuration: list[str] = Field(default_factory=list)


class EvaluationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_question: str | None = None
    metric: str
    metric_direction: Literal["higher_is_better", "lower_is_better", "neutral", "unknown"] = (
        "unknown"
    )
    subject: str
    subject_value: str | None = Field(
        default=None, description="Measured value belonging to the evaluated subject."
    )
    baseline: str | None = None
    baseline_value: str | None = Field(
        default=None, description="Measured value belonging to the named baseline."
    )
    improvement: str | None = None
    conditions: list[str] = Field(default_factory=list)
    evidence_pages: list[int] = Field(
        min_length=1, description="Source pages supporting the complete result."
    )


class SummaryEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    research_questions: list[str] = Field(default_factory=list)
    environment: EvaluationEnvironment = Field(default_factory=EvaluationEnvironment)
    baselines: list[str] = Field(default_factory=list)
    datasets: list[str] = Field(
        default_factory=list, description="Datasets used in the evaluation."
    )
    workloads: list[str] = Field(
        default_factory=list, description="Benchmarks, applications, or workloads evaluated."
    )
    metrics: list[str] = Field(default_factory=list)
    methodology: list[str] = Field(default_factory=list)
    results: list[EvaluationResult] = Field(default_factory=list)
    ablations: list[EvaluationResult] = Field(default_factory=list)


class SummaryDiscussion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    limitations: list[str] = Field(
        default_factory=list, description="Limitations and trade-offs acknowledged by the paper."
    )
    tradeoffs: list[str] = Field(default_factory=list)
    threats_to_validity: list[str] = Field(default_factory=list)
    applicability: list[str] = Field(default_factory=list)
    future_work: list[str] = Field(default_factory=list)
    conclusions: list[str] = Field(
        default_factory=list, description="Conclusions directly supported by the paper."
    )
    reusable_methods: list[str] = Field(
        default_factory=list,
        description="Methods that could be reused in related research.",
    )


class RelatedWorkGroup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    area: str
    representative_works: list[str] = Field(default_factory=list)
    relationship: str | None = None
    distinction: str | None = None
    evidence_pages: list[int] = Field(default_factory=list)


class SummaryRelatedWork(BaseModel):
    model_config = ConfigDict(extra="forbid")

    groups: list[RelatedWorkGroup] = Field(default_factory=list)


class StructuredSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["2"] = SUMMARY_SCHEMA_VERSION
    identity: SummaryIdentity
    classification: PaperClassification = Field(default_factory=PaperClassification)
    problem: SummaryProblem = Field(default_factory=SummaryProblem)
    contributions: list[Contribution] = Field(default_factory=list)
    design: SummaryDesign = Field(default_factory=SummaryDesign)
    implementation: SummaryImplementation = Field(default_factory=SummaryImplementation)
    evaluation: SummaryEvaluation = Field(default_factory=SummaryEvaluation)
    discussion: SummaryDiscussion = Field(default_factory=SummaryDiscussion)
    related_work: SummaryRelatedWork = Field(default_factory=SummaryRelatedWork)


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
    provider_health: ProviderHealthSnapshot | None = None,
    force: bool = False,
    provider: LlmProvider | None = None,
    execution_log_dir: Path | None = None,
    progress: ProgressCallback | None = None,
    llm_stats: LlmCallStats | None = None,
) -> SummaryResult:
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise SummaryError(f"Paper not found: {paper_id}")
    existing = get_artifact(database_path, paper_id, SUMMARY_ARTIFACT_KIND)
    if paper.status in {PaperStatus.SUMMARIZED, PaperStatus.OUTLINED} and not force:
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
    if force and paper.status is not PaperStatus.PARSED:
        update_paper_status(database_path, paper_id, PaperStatus.PARSED)

    try:
        prompts = load_summary_prompt_templates(
            summarization.facts_prompt_path,
            summarization.summary_prompt_path,
            summarization.repair_prompt_path,
        )
    except PromptTemplateError as exc:
        raise SummaryError(str(exc)) from exc

    if provider_health is not None:
        try:
            provider_health.require("llm")
        except ProviderUnavailableError as exc:
            raise SummaryError(str(exc)) from exc
    llm = TrackedLlmProvider(provider or OpenAICompatibleProvider(settings), llm_stats)
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
            prompts.facts,
        )
        report_progress(progress, "Generating structured summary...")
        raw_response = _generate(
            llm,
            _summary_prompt(prompts.summary, paper, facts),
            database_path,
            run_id,
            call_log_dir / "summary.json" if call_log_dir is not None else None,
            "summary",
            summarization.summary_max_output_tokens,
            LlmStage.SUMMARY,
        )
        summary = _validate_or_repair(
            raw_response.content,
            llm,
            database_path,
            run_id,
            call_log_dir,
            summarization.summary_max_output_tokens,
            progress,
            prompts.repair,
        )
        summary = summary.model_copy(update={"identity": _summary_identity(paper)})
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
        logger.error("summary failed: paper_id=%s error=%s", paper_id, exc)
        raise SummaryError(str(exc)) from exc
    except KeyboardInterrupt:
        finish_processing_run(database_path, run_id, error_message="interrupted")
        raise
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
    provider: TrackedLlmProvider,
    database_path: Path,
    run_id: str,
    progress: ProgressCallback | None,
    prompt_template: PromptTemplate,
) -> list[str]:
    chunks = _chunks(parsed.sections, max_chunk_characters)
    facts: list[str] = []
    for index, chunk in enumerate(chunks, start=1):
        digest = hashlib.sha256(f"{prompt_template.sha256}\0{chunk}".encode()).hexdigest()
        path = facts_dir / f"section-{digest}.json"
        if path.exists():
            try:
                cached = str(json.loads(path.read_text(encoding="utf-8"))["response"])
                facts.append(ExtractedFacts.model_validate_json(cached).model_dump_json())
                report_progress(progress, f"Reusing section facts {index}/{len(chunks)}.")
                continue
            except (OSError, KeyError, TypeError, ValueError, ValidationError):
                pass
        report_progress(progress, f"Summarizing section facts {index}/{len(chunks)}...")
        response = _generate_facts(
            provider,
            _facts_prompt(prompt_template, chunk),
            database_path,
            run_id,
            call_log_dir,
            index,
            len(chunks),
            max_output_tokens,
            progress,
        )
        try:
            validated = ExtractedFacts.model_validate_json(response.content).model_dump_json()
        except ValidationError as exc:
            raise SummaryError(f"Extracted facts failed schema validation: {exc}") from exc
        _atomic_write(
            path,
            json.dumps(
                {
                    "prompt_version": SUMMARY_PROMPT_VERSION,
                    "prompt_sha256": prompt_template.sha256,
                    "response": validated,
                }
            ).encode(),
        )
        facts.append(validated)
    return facts


def _generate_facts(
    provider: TrackedLlmProvider,
    prompt: str,
    database_path: Path,
    run_id: str,
    call_log_dir: Path | None,
    index: int,
    total: int,
    max_output_tokens: int,
    progress: ProgressCallback | None,
    *,
    max_attempts: int = 3,
) -> LlmResponse:
    attempt_tokens = max_output_tokens
    response: LlmResponse | None = None
    for attempt in range(1, max_attempts + 1):
        suffix = "" if attempt == 1 else f"-retry-{attempt}"
        response = _generate(
            provider,
            prompt,
            database_path,
            run_id,
            call_log_dir / f"facts-{index:03d}{suffix}.json" if call_log_dir is not None else None,
            f"facts {index}/{total}{suffix.replace('-', ' ')}",
            attempt_tokens,
            LlmStage.FACT,
        )
        truncated = response.finish_reason == "length"
        valid = False
        if truncated:
            try:
                ExtractedFacts.model_validate_json(response.content)
            except ValidationError:
                pass
            else:
                valid = True
        if not truncated or valid or attempt == max_attempts:
            return response
        attempt_tokens = min(attempt_tokens * 2, max_output_tokens * 4)
        report_progress(
            progress,
            f"Retrying section facts {index}/{total} with {attempt_tokens} output tokens "
            "(previous response was truncated)...",
        )
    assert response is not None
    return response


def _generate(
    provider: TrackedLlmProvider,
    prompt: str,
    database_path: Path,
    run_id: str,
    diagnostic_path: Path | None,
    label: str,
    max_tokens: int,
    purpose: LlmStage,
) -> LlmResponse:
    diagnostic = {
        "label": label,
        "provider": provider.provider_name,
        "model": provider.model,
        "max_tokens": max_tokens,
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
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
        response = provider.generate(purpose, prompt, max_tokens=max_tokens)
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
    provider: TrackedLlmProvider,
    database_path: Path,
    run_id: str,
    call_log_dir: Path | None,
    max_output_tokens: int,
    progress: ProgressCallback | None,
    repair_template: PromptTemplate,
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
                _repair_prompt(repair_template, current, str(exc)),
                database_path,
                run_id,
                call_log_dir / f"repair-{attempt + 1}.json" if call_log_dir is not None else None,
                f"repair {attempt + 1}/2",
                max_output_tokens,
                LlmStage.SUMMARY,
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


def _facts_prompt(template: PromptTemplate, chunk: str) -> str:
    return template.render(
        schema=json.dumps(ExtractedFacts.model_json_schema(), ensure_ascii=False),
        chunk=chunk,
    )


def _summary_prompt(
    template: PromptTemplate,
    paper: PaperRecord,
    facts: list[str],
) -> str:
    identity = {
        "title": paper.title,
        "authors": list(paper.authors),
        "year": paper.year,
        "venue": paper.venue,
        "doi": paper.doi,
        "arxiv_id": paper.arxiv_id,
    }
    return template.render(
        schema=json.dumps(StructuredSummary.model_json_schema(), ensure_ascii=False),
        identity=json.dumps(identity, ensure_ascii=False),
        facts=json.dumps([json.loads(fact) for fact in facts], ensure_ascii=False),
    )


def _summary_identity(paper: PaperRecord) -> SummaryIdentity:
    return SummaryIdentity(
        title=paper.title or paper.original_filename,
        authors=list(paper.authors),
        year=paper.year,
        venue=paper.venue,
        doi=paper.doi,
        arxiv_id=paper.arxiv_id,
    )


def _repair_prompt(template: PromptTemplate, raw: str, error: str) -> str:
    return template.render(
        schema=json.dumps(StructuredSummary.model_json_schema(), ensure_ascii=False),
        validation_error=error,
        candidate=raw,
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
