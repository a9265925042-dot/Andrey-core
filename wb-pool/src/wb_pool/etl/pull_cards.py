"""pull-cards — Seller API content list → wb_cards. Idempotent (UPSERT)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool.clients.wb_seller import WBAPIClient
from wb_pool.db import UnitOfWork
from wb_pool.logging_setup import get_logger
from wb_pool.models import WBCard

_log = get_logger(__name__)

_PAGE_LIMIT = 100


def _iso_to_epoch(value: Any) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


async def pull_my_cards(
    engine: AsyncEngine, *, token: str, ingest_run_id: int
) -> int:
    """Paginate POST /content/v2/get/cards/list → upsert wb_cards. Returns count."""
    now = int(datetime.now(UTC).timestamp())
    total = 0
    cursor: dict[str, Any] = {"limit": _PAGE_LIMIT}
    async with WBAPIClient.content(token=token) as client:
        while True:
            r = await client.post(
                "/content/v2/get/cards/list",
                json={"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
            )
            payload = r.json()
            cards = payload.get("cards", []) or payload.get("data", {}).get("cards", [])
            if not cards:
                break
            rows = []
            for c in cards:
                dims = c.get("dimensions") or {}
                rows.append(
                    {
                        "nm_id": int(c["nmID"]),
                        "nm_uuid": c.get("nmUUID"),
                        "imt_id": c.get("imtID"),
                        "vendor_code": str(c.get("vendorCode", "")),
                        "brand": c.get("brand"),
                        "title": c.get("title"),
                        "subject_id": c.get("subjectID"),
                        "subject_name": c.get("subjectName"),
                        "is_archived": 0,
                        "photos_json": json.dumps(c.get("photos") or [], ensure_ascii=False),
                        "sizes_json": json.dumps(c.get("sizes") or [], ensure_ascii=False),
                        "dimensions_length": dims.get("length"),
                        "dimensions_width": dims.get("width"),
                        "dimensions_height": dims.get("height"),
                        "dimensions_weight": dims.get("weightBrutto"),
                        "created_at_wb": _iso_to_epoch(c.get("createdAt")),
                        "updated_at_wb": _iso_to_epoch(c.get("updatedAt")) or now,
                        "fetched_at": now,
                        "ingest_run_id": ingest_run_id,
                    }
                )
            async with UnitOfWork(engine) as uow:
                stmt = sqlite_insert(WBCard).values(rows)
                update_cols = {
                    k: stmt.excluded[k]
                    for k in rows[0]
                    if k != "nm_id"
                }
                stmt = stmt.on_conflict_do_update(
                    index_elements=["nm_id"], set_=update_cols
                )
                await uow.session.execute(stmt)
                await uow.commit()
            total += len(rows)
            cursor_meta = payload.get("cursor", {})
            if len(cards) < _PAGE_LIMIT:
                break
            cursor = {
                "limit": _PAGE_LIMIT,
                "updatedAt": cursor_meta.get("updatedAt"),
                "nmID": cursor_meta.get("nmID"),
            }
    _log.info("pull_cards_done", total=total)
    return total


__all__ = ["pull_my_cards"]
