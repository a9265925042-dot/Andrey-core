"""cmp_funnel_daily — daily funnel data per nm × date."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class CmpFunnelDaily(Base):
    __tablename__ = "cmp_funnel_daily"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    comparison_id: Mapped[str] = mapped_column(
        Text, ForeignKey("cmp_groups.comparison_id"), nullable=False
    )
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    date: Mapped[int] = mapped_column(Integer, nullable=False)
    open_card_count: Mapped[int] = mapped_column(Integer, nullable=False)
    add_to_cart_count: Mapped[int] = mapped_column(Integer, nullable=False)
    orders_count: Mapped[int] = mapped_column(Integer, nullable=False)
    orders_sum_kopeks: Mapped[int] = mapped_column(Integer, nullable=False)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("comparison_id", "nm_id", "date"),
        Index("ix_cmp_funnel_nm_date", "nm_id", "date"),
    )
