from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

from passagen.config import MetadataSettings, ParsingSettings
from passagen.metadata_service import MetadataResolutionError, resolve_paper_metadata
from passagen.models import PaperStatus
from passagen.parsing_service import PaperParsingError, parse_paper
from passagen.progress import ProgressCallback, report_progress
from passagen.repository import PaperRecord, get_paper, list_papers

LATEST_IMPLEMENTED_STATUS = PaperStatus.PARSED
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
    refresh: bool = False,
    progress: ProgressCallback | None = None,
) -> UpdateResult:
    papers = _select_papers(database_path, paper_id)
    logger.info(
        "update started: target=%s refresh=%s selected=%s latest_status=%s",
        paper_id or "all",
        refresh,
        len(papers),
        LATEST_IMPLEMENTED_STATUS.value,
    )
    report_progress(progress, f"Selected {len(papers)} paper(s) for update.")
    result = UpdateResult()
    total = len(papers)
    for index, paper in enumerate(papers, start=1):
        if not refresh and paper.status not in _UPDATE_PENDING_STATUSES:
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
            needs_metadata = refresh or current.status in {
                PaperStatus.DISCOVERED,
                PaperStatus.FAILED,
            }
            stage_total = 2 if needs_metadata else 1
            if needs_metadata:
                logger.info("update stage started: paper_id=%s stage=metadata", paper.id)
                _report_paper_progress(
                    progress,
                    index,
                    total,
                    paper,
                    "metadata",
                    "starting.",
                    stage_number=1,
                    stage_total=stage_total,
                )
                resolution = resolve_paper_metadata(
                    database_path,
                    data_dir,
                    paper.id,
                    metadata_settings,
                    refresh=refresh,
                    progress=lambda message, index=index, paper=paper, stage_total=stage_total: (
                        _report_paper_progress(
                            progress,
                            index,
                            total,
                            paper,
                            "metadata",
                            message,
                            stage_number=1,
                            stage_total=stage_total,
                        )
                    ),
                )
                current = resolution.paper
                warnings.extend(resolution.warnings)
                logger.info("update stage finished: paper_id=%s stage=metadata", paper.id)
            logger.info("update stage started: paper_id=%s stage=full_text", paper.id)
            _report_paper_progress(
                progress,
                index,
                total,
                paper,
                "full text",
                "starting.",
                stage_number=stage_total,
                stage_total=stage_total,
            )
            parsing = parse_paper(
                database_path,
                data_dir,
                paper.id,
                parsing_settings,
                refresh=refresh,
                progress=lambda message, index=index, paper=paper, stage_total=stage_total: (
                    _report_paper_progress(
                        progress,
                        index,
                        total,
                        paper,
                        "full text",
                        message,
                        stage_number=stage_total,
                        stage_total=stage_total,
                    )
                ),
            )
            warnings.extend(parsing.warnings)
            logger.info("update stage finished: paper_id=%s stage=full_text", paper.id)
        except (MetadataResolutionError, PaperParsingError) as exc:
            logger.error("update paper failed: paper_id=%s error=%s", paper.id, exc)
            result.failures.append(UpdateFailure(paper.id, str(exc)))
            _report_paper_progress(
                progress, index, total, paper, "failed", "update failed; continuing."
            )
            continue
        if parsing.updated:
            result.updated.append(parsing.paper)
            logger.info(
                "update paper finished: paper_id=%s status=%s title=%s",
                paper.id,
                parsing.paper.status.value,
                parsing.paper.title,
            )
            _report_paper_progress(progress, index, total, paper, "complete", "updated.")
        else:
            result.skipped.append(parsing.paper)
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
