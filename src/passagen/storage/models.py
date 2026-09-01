from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class PaperRow(Base):
    __tablename__ = "papers"
    __table_args__ = (
        CheckConstraint(
            "status IN ('discovered', 'parsed', 'metadata_resolved', 'summarized', 'outlined')"
        ),
        Index("ux_papers_doi", "doi", unique=True),
        Index("ux_papers_arxiv_id", "arxiv_id", unique=True),
        Index("ux_papers_pdf_sha256", "pdf_sha256", unique=True),
    )

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    title: Mapped[str | None] = mapped_column(Text)
    authors_json: Mapped[str | None] = mapped_column(Text)
    year: Mapped[int | None] = mapped_column(Integer)
    venue: Mapped[str | None] = mapped_column(Text)
    doi: Mapped[str | None] = mapped_column(Text)
    arxiv_id: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str | None] = mapped_column(Text)
    metadata_sources_json: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'{}'")
    )
    original_filename: Mapped[str] = mapped_column(Text, nullable=False)
    pdf_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'discovered'"))
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    artifacts: Mapped[list[ArtifactRow]] = relationship(
        back_populates="paper", passive_deletes=True
    )
    processing_runs: Mapped[list[ProcessingRunRow]] = relationship(
        back_populates="paper", passive_deletes=True
    )


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (Index("ix_artifacts_paper_id", "paper_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        Text, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    paper: Mapped[PaperRow] = relationship(back_populates="artifacts")


class ProcessingRunRow(Base):
    __tablename__ = "processing_runs"
    __table_args__ = (Index("ix_processing_runs_paper_id", "paper_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    paper_id: Mapped[str] = mapped_column(
        Text, ForeignKey("papers.id", ondelete="CASCADE"), nullable=False
    )
    stage: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)
    error_message: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    finished_at: Mapped[str | None] = mapped_column(Text)

    paper: Mapped[PaperRow] = relationship(back_populates="processing_runs")
    llm_calls: Mapped[list[LlmCallRow]] = relationship(
        back_populates="processing_run", passive_deletes=True
    )


class LlmCallRow(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_processing_run_id", "processing_run_id"),)

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    processing_run_id: Mapped[str] = mapped_column(
        Text, ForeignKey("processing_runs.id", ondelete="CASCADE"), nullable=False
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_version: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    processing_run: Mapped[ProcessingRunRow] = relationship(back_populates="llm_calls")
