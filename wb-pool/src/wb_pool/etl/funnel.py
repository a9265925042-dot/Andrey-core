"""Funnel ETL — cabinet comparison response → cmp_groups + cmp_funnel_daily."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from wb_pool.models import CmpFunnelDaily, CmpGroup

# WB отдаёт даты в московском времени; храним epoch полуночи MSK.
MSK = timezone(timedelta(hours=3))

_CMP_ID_SUBJECT_RE = re.compile(r"^sub(\d+)-")


@dataclass(frozen=True)
class EtlResult:
    rows_written: int


def iso_date_to_epoch_midnight_utc(value: str) -> int:
    d = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=UTC)
    return int(d.timestamp())


def iso_date_to_epoch_midnight_msk(value: str) -> int:
    d = datetime.strptime(value[:10], "%Y-%m-%d").replace(tzinfo=MSK)
    return int(d.timestamp())


def extract_subject_id_from_cmp_id(comparison_id: str) -> int:
    m = _CMP_ID_SUBJECT_RE.match(comparison_id)
    if m is None:
        raise ValueError(f"Cannot parse subject_id from comparison_id: {comparison_id}")
    return int(m.group(1))


async def seed_cmp_group_from_payload(
    session: AsyncSession,
    *,
    comparison_id: str,
    payload: dict[str, Any],
    ingest_run_id: int,
    **extras: Any,
) -> None:
    """Создать row в cmp_groups (если не существует). Idempotent."""
    _ = extras  # e.g. category_job_id — принимаем, не используем
    now = int(datetime.now(UTC).timestamp())
    data = payload.get("data", {})
    products = data.get("products", [])
    nm_ids = sorted(
        int(p["product"]["nmId"] if "product" in p else p["nmId"]) for p in products
    )
    period = data.get("period", {})
    period_start_epoch = iso_date_to_epoch_midnight_utc(str(period["start"]))
    period_end_epoch = iso_date_to_epoch_midnight_utc(str(period["end"]))
    subject_id = extract_subject_id_from_cmp_id(comparison_id)
    stmt = sqlite_insert(CmpGroup).values(
        comparison_id=comparison_id,
        subject_id=subject_id,
        nm_ids_json=json.dumps(nm_ids),
        period_start=period_start_epoch,
        period_end=period_end_epoch,
        created_at=now,
        expires_at=now + 30 * 86400,
        status="ready",
        main_nm_id=nm_ids[0] if nm_ids else None,
        ingest_run_id=ingest_run_id,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["comparison_id"])
    await session.execute(stmt)


async def upsert_funnel_daily_from_comparison(
    session: AsyncSession,
    *,
    comparison_id: str,
    comparison_response: dict[str, Any],
    ingest_run_id: int,
) -> EtlResult:
    """Parse dayDynamics → upsert cmp_funnel_daily. Rubles → kopeks (×100)."""
    rows: list[dict[str, Any]] = []
    for entry in comparison_response.get("data", {}).get("dayDynamics", []):
        rows.append(
            {
                "comparison_id": comparison_id,
                "nm_id": int(entry["nmID"]),
                "date": iso_date_to_epoch_midnight_msk(str(entry["dt"])),
                "open_card_count": int(entry["openCardCount"]),
                "add_to_cart_count": int(entry["addToCartCount"]),
                "orders_count": int(entry["orderCount"]),
                "orders_sum_kopeks": round(float(entry["orderSum"]) * 100),
                "ingest_run_id": ingest_run_id,
            }
        )
    if not rows:
        return EtlResult(rows_written=0)
    stmt = sqlite_insert(CmpFunnelDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["comparison_id", "nm_id", "date"],
        set_={
            k: stmt.excluded[k]
            for k in (
                "open_card_count",
                "add_to_cart_count",
                "orders_count",
                "orders_sum_kopeks",
                "ingest_run_id",
            )
        },
    )
    await session.execute(stmt)
    return EtlResult(rows_written=len(rows))


async def write_failed_to_dlq(
    session: AsyncSession,
    *,
    comparison_id: str,
    nm_ids: list[int],
    error: str,
    ingest_run_id: int,
) -> None:
    from wb_pool.models import CmpDlq

    stmt = sqlite_insert(CmpDlq).values(
        comparison_id=comparison_id,
        nm_ids_json=json.dumps(sorted(nm_ids)),
        error_msg=error[:2000],
        retry_count=0,
        created_at=int(datetime.now(UTC).timestamp()),
        ingest_run_id=ingest_run_id,
    )
    await session.execute(stmt)


__all__ = [
    "MSK",
    "EtlResult",
    "extract_subject_id_from_cmp_id",
    "iso_date_to_epoch_midnight_msk",
    "iso_date_to_epoch_midnight_utc",
    "seed_cmp_group_from_payload",
    "upsert_funnel_daily_from_comparison",
    "write_failed_to_dlq",
]
