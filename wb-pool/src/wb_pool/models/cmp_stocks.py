"""cmp_warehouse_metrics + cmp_size_stocks — склад и остатки."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class CmpWarehouseMetric(Base):
    __tablename__ = "cmp_warehouse_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cmp_groups.comparison_id"), nullable=False
    )
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    warehouse_name: Mapped[str] = mapped_column(Text, nullable=False)
    metric_type: Mapped[str] = mapped_column(Text, nullable=False)
    metric_value: Mapped[int] = mapped_column(Integer, nullable=False)
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_date: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "comparison_id",
            "nm_id",
            "warehouse_name",
            "metric_type",
            "period_start",
            "period_end",
            "snapshot_date",
        ),
    )


class CmpSizeStock(Base):
    __tablename__ = "cmp_size_stocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cmp_groups.comparison_id"), nullable=False
    )
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    size_name: Mapped[str] = mapped_column(Text, nullable=False)
    stock_count: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[int] = mapped_column(Integer, nullable=False)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("comparison_id", "nm_id", "size_name", "date"),
    )
