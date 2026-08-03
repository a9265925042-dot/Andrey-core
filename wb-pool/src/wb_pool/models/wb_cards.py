"""wb_cards — мои карточки (Seller API content)."""

from __future__ import annotations

from sqlalchemy import Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from wb_pool.models.base import Base


class WBCard(Base):
    __tablename__ = "wb_cards"

    nm_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    nm_uuid: Mapped[str | None] = mapped_column(Text, nullable=True)
    imt_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    vendor_code: Mapped[str] = mapped_column(Text, nullable=False)
    brand: Mapped[str | None] = mapped_column(Text, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    subject_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_archived: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    photos_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    sizes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    dimensions_length: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimensions_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimensions_height: Mapped[float | None] = mapped_column(Float, nullable=True)
    dimensions_weight: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at_wb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at_wb: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[int] = mapped_column(Integer, nullable=False)
    ingest_run_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("raw_runs.id"), nullable=False
    )

    __table_args__ = (
        Index("ix_wb_cards_subject", "subject_id", "is_archived"),
        Index("ix_wb_cards_brand", "brand"),
    )
