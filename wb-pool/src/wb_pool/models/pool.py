"""analytic_category_pool — исторический пул конкурентов (optional Worker 4)."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class AnalyticCategoryPool(Base):
    __tablename__ = "analytic_category_pool"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    my_nm_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_id: Mapped[int] = mapped_column(Integer, nullable=False)
    competitor_nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_date: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_top30: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("subject_id", "competitor_nm_id", "snapshot_date"),
        Index("ix_analytic_category_pool_subject", "subject_id", "snapshot_date"),
    )
