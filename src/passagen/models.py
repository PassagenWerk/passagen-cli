from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from enum import StrEnum


class PaperStatus(StrEnum):
    DISCOVERED = "discovered"
    PARSED = "parsed"
    METADATA_RESOLVED = "metadata_resolved"
    SUMMARIZED = "summarized"
    OUTLINED = "outlined"
    COMPLETED = "completed"
    FAILED = "failed"


ALLOWED_STATUS_TRANSITIONS: dict[PaperStatus, set[PaperStatus]] = {
    PaperStatus.DISCOVERED: {PaperStatus.PARSED, PaperStatus.FAILED},
    PaperStatus.PARSED: {PaperStatus.METADATA_RESOLVED, PaperStatus.FAILED},
    PaperStatus.METADATA_RESOLVED: {PaperStatus.SUMMARIZED, PaperStatus.FAILED},
    PaperStatus.SUMMARIZED: {PaperStatus.OUTLINED, PaperStatus.FAILED},
    PaperStatus.OUTLINED: {PaperStatus.COMPLETED, PaperStatus.FAILED},
    PaperStatus.COMPLETED: set(),
    PaperStatus.FAILED: {
        PaperStatus.DISCOVERED,
        PaperStatus.PARSED,
        PaperStatus.METADATA_RESOLVED,
        PaperStatus.SUMMARIZED,
        PaperStatus.OUTLINED,
    },
}


class InvalidStatusTransition(ValueError):
    pass


@dataclass(slots=True)
class Paper:
    original_filename: str
    pdf_sha256: str
    file_size_bytes: int
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    title: str | None = None
    authors: list[str] = field(default_factory=list)
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    source_url: str | None = None
    status: PaperStatus = PaperStatus.DISCOVERED

    def transition_to(self, target: PaperStatus) -> None:
        if target not in ALLOWED_STATUS_TRANSITIONS[self.status]:
            raise InvalidStatusTransition(
                f"Cannot transition paper from {self.status.value} to {target.value}"
            )
        self.status = target
