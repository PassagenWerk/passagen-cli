from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from passagen.config import MetadataSettings
from passagen.metadata_service import MetadataResolutionError, resolve_paper_metadata
from passagen.models import PaperStatus
from passagen.repository import PaperRecord, get_paper, list_papers

LATEST_IMPLEMENTED_STATUS = PaperStatus.METADATA_RESOLVED
_METADATA_PENDING_STATUSES = {PaperStatus.DISCOVERED, PaperStatus.FAILED}


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
    paper_id: str | None = None,
    *,
    refresh: bool = False,
) -> UpdateResult:
    papers = _select_papers(database_path, paper_id)
    result = UpdateResult()
    for paper in papers:
        if not refresh and paper.status not in _METADATA_PENDING_STATUSES:
            result.skipped.append(paper)
            continue
        try:
            resolution = resolve_paper_metadata(
                database_path,
                data_dir,
                paper.id,
                metadata_settings,
                refresh=refresh,
            )
        except MetadataResolutionError as exc:
            result.failures.append(UpdateFailure(paper.id, str(exc)))
            continue
        if resolution.updated:
            result.updated.append(resolution.paper)
        else:
            result.skipped.append(resolution.paper)
        result.warnings.extend(UpdateFailure(paper.id, warning) for warning in resolution.warnings)
    return result


def _select_papers(database_path: Path, paper_id: str | None) -> list[PaperRecord]:
    if paper_id is None:
        return list_papers(database_path)
    paper = get_paper(database_path, paper_id)
    if paper is None:
        raise UpdateTargetError(f"Paper not found: {paper_id}")
    return [paper]
