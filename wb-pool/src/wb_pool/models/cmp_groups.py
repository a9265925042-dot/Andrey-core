"""cmp_groups — купленные группы (5 nm/группа) + cmp_dlq для failed purchases."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class CmpGroup(Base):
    __tablename__ = "cmp_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(Text, nullable=True)
    nm_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, default="purchasing")
    main_nm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wb_export_uuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingested_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        Index("ix_cmp_groups_subject_created", "subject_id", "created_at"),
        Index("ix_cmp_groups_status", "status"),
    )


class CmpDlq(Base):
    __tablename__ = "cmp_dlq"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(Text, nullable=False)
    nm_ids_json: Mapped[str] = mapped_column(Text, nullable=False)
    error_msg: Mapped[str | None] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False)
    resolved_at: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (Index("ix_cmp_dlq_unresolved", "resolved_at"),)
