"""MPStats tables — keywords per моя карточка + listing baseline cache.

mpstats_listing_rows хранит ТОЛЬКО membership (nm_id + source + period + rank
в extra_json). Числовые поля MPStats (revenue/sales/balance) сюда сознательно
НЕ пишутся — они ненадёжные (см. SKILL.md, правило 3).
"""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class MPStatsKeyword(Base):
    __tablename__ = "mpstats_keywords"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    keyword: Mapped[str] = mapped_column(Text, nullable=False)
    date: Mapped[int] = mapped_column(Integer, nullable=False)
    avg_position: Mapped[int] = mapped_column(Integer, nullable=False)
    traffic: Mapped[int] = mapped_column(Integer, nullable=False)
    visibility_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("nm_id", "keyword", "date"),
        Index(
            "ix_mpstats_keywords_nm_pos_traffic", "nm_id", "avg_position", "traffic"
        ),
    )


class MPStatsListingRow(Base):
    __tablename__ = "mpstats_listing_rows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    nm_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    source_query: Mapped[str] = mapped_column(Text, nullable=False)
    period_start: Mapped[int] = mapped_column(Integer, nullable=False)
    period_end: Mapped[int] = mapped_column(Integer, nullable=False)
    extra_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("nm_id", "source_type", "source_query", "period_start", "period_end"),
    )
