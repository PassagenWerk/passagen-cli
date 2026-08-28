from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path

from passagen.config import LlmSettings, MetadataSettings, ParsingSettings
from passagen.llm import LlmProvider
from passagen.metadata_service import MetadataResolutionError, resolve_paper_metadata
from passagen.models import PaperStatus
from passagen.parsing_service import PaperParsingError, parse_paper
from passagen.progress import ProgressCallback, report_progress
from passagen.repository import PaperRecord, get_paper, list_papers
from passagen.summarization import SummaryError, summarize_paper

LATEST_IMPLEMENTED_STATUS = PaperStatus.SUMMARIZED
_UPDATE_PENDING_STATUSES = {
    PaperStatus.DISCOVERED,
    PaperStatus.FAILED,
    PaperStatus.METADATA_RESOLVED,
}
logger = logging.getLogger(__name__)


class UpdateTargetError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class UpdateFailure:
    paper_id: str
    message: str


@dataclass(slots=True)
class UpdateResult:
    target_status: PaperStatus
    updated: list[PaperRecord] = field(default_factory=list)
    skipped: list[PaperRecord] = field(default_factory=list)
    warnings: list[UpdateFailure] = field(default_factory=list)
    failures: list[UpdateFailure] = field(default_factory=list)


def update_papers(
    database_path: Path,
    data_dir: Path,
    metadata_settings: MetadataSettings,
    parsing_settings: ParsingSettings,
    paper_id: str | None = None,
    *,
    llm_settings: LlmSettings,
    summary_provider: LlmProvider | None = None,
    force: bool = False,
    progress: ProgressCallback | None = None,
) -> UpdateResult:
    papers = _select_papers(database_path, paper_id)
    summarize = True
    target_status = LATEST_IMPLEMENTED_STATUS
    logger.info(
        "update started: target=%s force=%s selected=%s latest_status=%s",
        paper_id or "all",
        force,
        len(papers),
        target_status.value,
    )
    report_progress(progress, f"Selected {len(papers)} paper(s) for update.")
    result = UpdateResult(target_status=target_status)
    total = len(papers)
    for index, paper in enumerate(papers, start=1):
        pending_statuses = _UPDATE_PENDING_STATUSES | {PaperStatus.PARSED}
        if not force and paper.status not in pending_statuses:
            logger.info(
                "update skipped: paper_id=%s status=%s reason=already_at_or_beyond_target",
                paper.id,
                paper.status.value,
            )
            result.skipped.append(paper)
            _report_paper_progress(
                progress,
                index,
                total,
                paper,
                "selection",
                "already at or beyond the target status; skipping.",
            )
            continue
        logger.info(
            "update paper started: paper_id=%s status=%s filename=%s",
            paper.id,
            paper.status.value,
            paper.original_filename,
        )
        _report_paper_progress(progress, index, total, paper, "selection", "starting update.")
        try:
            current = paper
            warnings: list[str] = []
            needs_metadata = force or current.status in {
                PaperStatus.DISCOVERED,
                PaperStatus.FAILED,
            }
            needs_parsing = (
                force
                or needs_metadata
                or current.status
                in {
                    PaperStatus.METADATA_RESOLVED,
                    PaperStatus.FAILED,
                }
            )
            stage_total = int(needs_metadata) + int(needs_parsing) + int(summarize)
            stage_number = 0
            if needs_metadata:
                stage_number += 1
                logger.info("update stage started: paper_id=%s stage=metadata", paper.id)
                _report_paper_progress(
                    progress,
                    index,
                    total,
                    paper,
                    "metadata",
                    "starting.",
                    stage_number=stage_number,
                    stage_total=stage_total,
                )
                resolution = resolve_paper_metadata(
                    database_path,
                    data_dir,
                    paper.id,
                    metadata_settings,
                    force=force,
                    progress=partial(
                        _report_paper_progress,
                        progress,
                        index,
                        total,
                        paper,
                        "metadata",
                        stage_number=1,
                        stage_total=stage_total,
                    ),
                )
                current = resolution.paper
                warnings.extend(resolution.warnings)
                logger.info("update stage finished: paper_id=%s stage=metadata", paper.id)
            if needs_parsing:
                stage_number += 1
                logger.info("update stage started: paper_id=%s stage=full_text", paper.id)
                _report_paper_progress(
                    progress,
                    index,
                    total,
                    paper,
                    "full text",
                    "starting.",
                    stage_number=stage_number,
                    stage_total=stage_total,
                )
                parsing = parse_paper(
                    database_path,
                    data_dir,
                    paper.id,
                    parsing_settings,
                    force=force,
                    progress=partial(
                        _report_paper_progress,
                        progress,
                        index,
                        total,
                        paper,
                        "full text",
                        stage_number=stage_number,
                        stage_total=stage_total,
                    ),
                )
                current = parsing.paper
                warnings.extend(parsing.warnings)
                logger.info("update stage finished: paper_id=%s stage=full_text", paper.id)
            if summarize:
                stage_number += 1
                logger.info("update stage started: paper_id=%s stage=summarize", paper.id)
                _report_paper_progress(
                    progress,
                    index,
                    total,
                    paper,
                    "summary",
                    "starting.",
                    stage_number=stage_number,
                    stage_total=stage_total,
                )
                summary = summarize_paper(
                    database_path,
                    data_dir,
                    paper.id,
                    llm_settings,
                    force=force,
                    provider=summary_provider,
                    progress=partial(
                        _report_paper_progress,
                        progress,
                        index,
                        total,
                        paper,
                        "summary",
                        stage_number=stage_number,
                        stage_total=stage_total,
                    ),
                )
                current = summary.paper
                logger.info("update stage finished: paper_id=%s stage=summarize", paper.id)
        except (MetadataResolutionError, PaperParsingError, SummaryError) as exc:
            logger.error("update paper failed: paper_id=%s error=%s", paper.id, exc)
            result.failures.append(UpdateFailure(paper.id, str(exc)))
            _report_paper_progress(
                progress, index, total, paper, "failed", "update failed; continuing."
            )
            continue
        if needs_metadata or needs_parsing or summarize:
            result.updated.append(current)
            logger.info(
                "update paper finished: paper_id=%s status=%s title=%s",
                paper.id,
                current.status.value,
                current.title,
            )
            _report_paper_progress(progress, index, total, paper, "complete", "updated.")
        else:
            result.skipped.append(current)
            logger.info("update paper skipped by stage: paper_id=%s", paper.id)
        result.warnings.extend(UpdateFailure(paper.id, warning) for warning in warnings)
        for warning in warnings:
            logger.warning("update paper warning: paper_id=%s warning=%s", paper.id, warning)
    logger.info(
        "update finished: updated=%s skipped=%s warnings=%s failed=%s",
        len(result.updated),
        len(result.skipped),
        len(result.warnings),
        len(result.failures),
    )
    report_progress(
        progress,
        f"Update complete: {len(result.updated)} updated, "
        f"{len(result.skipped)} skipped, {len(result.failures)} failed.",
    )
    return result


def _report_paper_progress(
    progress: ProgressCallback | None,
    index: int,
    total: int,
    paper: PaperRecord,
    stage: str,
    message: str,
    *,
    stage_number: int | None = None,
    stage_total: int | None = None,
) -> None:
    stage_label = (
        f"stage {stage_number}/{stage_total}: {stage}"
        if stage_number is not None and stage_total is not None
        else stage
    )
    report_progress(
        progress,
        f"Paper {index}/{total} [{stage_label}]: "
        f"{paper.title or paper.original_filename}: {message}",
    )


def _select_papers(database_path: Path, paper_id: str | None) -> list[PaperRecord]:
    if paper_id is None:
        return list_papers(database_path)
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise UpdateTargetError(f"Paper not found: {paper_id}")
    return [paper]
