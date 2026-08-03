"""cmp_search_queries + cmp_search_query_per_nm — keywords с conversions."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class CmpSearchQuery(Base):
    __tablename__ = "cmp_search_queries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cmp_groups.comparison_id"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency: Mapped[int] = mapped_column(Integer, nullable=False)
    frequency_dynamics: Mapped[int | None] = mapped_column(Integer, nullable=True)
    snapshot_date: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "comparison_id", "keyword", "period_start", "period_end", "snapshot_date"
        ),
        Index("ix_cmp_search_queries_kw", "keyword"),
    )


class CmpSearchQueryPerNm(Base):
    __tablename__ = "cmp_search_query_per_nm"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cmp_groups.comparison_id"), nullable=False
    )
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)
    cart_from_search_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    order_from_search_raw: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cart_conv_pct_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    order_conv_pct_raw: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_rounded_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    snapshot_date: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingested_at: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "comparison_id", "keyword", "nm_id", "period_start", "period_end", "snapshot_date"
        ),
        Index("ix_cmp_search_query_per_nm_nm", "nm_id", "keyword"),
    )
