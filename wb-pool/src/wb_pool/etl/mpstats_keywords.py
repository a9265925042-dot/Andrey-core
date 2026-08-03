"""pull-mpstats-keywords — MPStats keywords per моя карточка → mpstats_keywords.

⚠ MPStats item-keywords endpoint не описан в skill docs (skill приводит только
/category/items). Реализация ниже использует общеизвестный
GET https://mpstats.io/api/wb/get/item/{nm}/by_keywords и tolerant-парсер.
Если MPStats вернёт другой формат — поправь `_parse_keywords_payload`
по фактическому JSON (сохрани sample через --debug-dump).
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool.db import UnitOfWork
from wb_pool.logging_setup import get_logger
from wb_pool.models import MPStatsKeyword

_log = get_logger(__name__)

_ITEM_KEYWORDS_URL = "https://mpstats.io/api/wb/get/item/{nm}/by_keywords"


def _parse_keywords_payload(payload: Any) -> list[dict[str, Any]]:
    """Tolerant разбор ответа by_keywords → [{keyword, avg_position, traffic, visibility_pct}]."""
    out: list[dict[str, Any]] = []

    def _add(kw: str, meta: dict[str, Any]) -> None:
        pos = meta.get("avgPos") or meta.get("avg_position") or meta.get("pos") or 0
        traffic = (
            meta.get("traffic")
            or meta.get("frequency")
            or meta.get("count")
            or meta.get("total")
            or 0
        )
        vis = meta.get("visibility") or meta.get("visibility_pct")
        try:
            out.append(
                {
                    "keyword": kw.strip().lower(),
                    "avg_position": round(float(pos)),
                    "traffic": round(float(traffic)),
                    "visibility_pct": float(vis) if vis is not None else None,
                }
            )
        except (ValueError, TypeError):
            return

    if isinstance(payload, dict):
        words = payload.get("words") or payload.get("data") or payload
        if isinstance(words, dict):
            for kw, meta in words.items():
                if isinstance(meta, dict):
                    _add(str(kw), meta)
        elif isinstance(words, list):
            for item in words:
                if isinstance(item, dict):
                    kw = item.get("word") or item.get("keyword") or item.get("name")
                    if kw:
                        _add(str(kw), item)
    return [r for r in out if r["keyword"]]


async def pull_mpstats_keywords(
    engine: AsyncEngine,
    *,
    token: str,
    subject_id: int,
    ingest_run_id: int,
    max_cards: int = 20,
    debug_dump_dir: Path | None = None,
) -> int:
    """Fetch keywords для моих карточек subject'а → upsert mpstats_keywords."""
    async with engine.connect() as conn:
        res = await conn.execute(
            text(
                "SELECT nm_id FROM wb_cards "
                "WHERE subject_id = :sid AND is_archived = 0 "
                "ORDER BY nm_id LIMIT :lim"
            ),
            {"sid": subject_id, "lim": max_cards},
        )
        nm_ids = [int(row[0]) for row in res]
    if not nm_ids:
        _log.warning("no_wb_cards_for_subject", subject_id=subject_id)
        return 0

    yesterday = date.today() - timedelta(days=1)
    d1 = (yesterday - timedelta(days=30)).isoformat()
    d2 = yesterday.isoformat()
    date_epoch = int(
        datetime.combine(yesterday, datetime.min.time(), tzinfo=UTC).timestamp()
    )

    total = 0
    async with httpx.AsyncClient(
        timeout=30, headers={"X-Mpstats-TOKEN": token, "Content-Type": "application/json"}
    ) as client:
        for nm in nm_ids:
            try:
                r = await client.get(
                    _ITEM_KEYWORDS_URL.format(nm=nm),
                    params={"d1": d1, "d2": d2, "full": "true"},
                )
                r.raise_for_status()
                payload = r.json()
            except Exception as exc:
                _log.warning("mpstats_keywords_fetch_failed", nm=nm, error=repr(exc))
                continue
            if debug_dump_dir is not None:
                debug_dump_dir.mkdir(parents=True, exist_ok=True)
                (debug_dump_dir / f"by_keywords-{nm}.json").write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2)
                )
            parsed = _parse_keywords_payload(payload)
            if not parsed:
                continue
            rows = [
                {
                    "nm_id": nm,
                    "keyword": p["keyword"],
                    "date": date_epoch,
                    "avg_position": p["avg_position"],
                    "traffic": p["traffic"],
                    "visibility_pct": p["visibility_pct"],
                    "ingest_run_id": ingest_run_id,
                }
                for p in parsed
            ]
            async with UnitOfWork(engine) as uow:
                stmt = sqlite_insert(MPStatsKeyword).values(rows)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["nm_id", "keyword", "date"],
                    set_={
                        "avg_position": stmt.excluded.avg_position,
                        "traffic": stmt.excluded.traffic,
                        "visibility_pct": stmt.excluded.visibility_pct,
                        "ingest_run_id": stmt.excluded.ingest_run_id,
                    },
                )
                await uow.session.execute(stmt)
                await uow.commit()
            total += len(rows)
    _log.info("pull_mpstats_keywords_done", subject_id=subject_id, rows=total)
    return total


__all__ = ["pull_mpstats_keywords"]
