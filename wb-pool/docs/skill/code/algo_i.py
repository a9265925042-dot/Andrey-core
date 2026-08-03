# ruff: noqa: RUF002
"""Algorithm I orchestrator — independent layers pool builder.

Epic market-yadm. Philosophy: each layer (SERP main, SERP narrow, MPStats 3-window)
is an independent добытчик that applies the ``feedbacks_threshold`` filter on its
own. Their outputs are unioned and deduplicated. No composite score, no trim,
no cabinet/sticky lookup.

Mantra: «не проебать топа» — каждый из трёх методов сам по себе должен подбирать
идеально, не сравниваясь с предыдущими шагами. Если на шаге MPStats нашлась
карточка которой не было в SERP — она гарантированно в pool. Никакая
композитная формула не может её "выбить".

Pure helpers reused from Algorithm H:
  * title_kw_extractor.pick_narrow_keyword
  * serp_with_feedbacks.fetch_serp_with_feedbacks
  * mpstats_topup.fetch_mpstats_top_n_with_comments
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool.discovery.algo_i_types import (
    AlgorithmIConfig,
    AlgorithmIResult,
)
from wb_pool.discovery.serp_with_feedbacks import (
    fetch_serp_with_feedbacks,
)
from wb_pool.discovery.title_kw_extractor import (
    pick_narrow_keyword,
)
from wb_pool.discovery.mpstats_layer import (
    fetch_mpstats_top_n_with_comments,
)
from wb_pool.config import get_settings
from wb_pool.logging_setup import get_logger
from wb_pool.db import UnitOfWork
from wb_pool.clients.card_detail import CardDetail, CardDetailFetcher
from wb_pool.clients.wb_seller import WBAPIClient
from wb_pool.clients.sales_funnel import SalesFunnelFetcher

_log = get_logger(__name__)
_BRAND_MARKERS = {"semily", "семили"}

# MPStats baseline windows (fixed per spec).
_JUNE_2025_START = date(2025, 6, 1)
_JUNE_2025_END = date(2025, 6, 30)
_YEAR_2025_START = date(2025, 1, 1)
_YEAR_2025_END = date(2025, 12, 31)


async def fetch_serp_main_layer(
    *,
    subject_name: str,
    top_n: int,
    feedbacks_threshold: int,
    max_retries: int = 2,
    retry_delay_sec: int = 20,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Layer A — fetch top-N SERP results for the category-level query.

    Если SERP вернул 0 (обычно из-за HTTP 429 throttle от WB), делает короткий
    backoff и пробует ещё раз. Это критично потому что Layer A это всего 1
    запрос — если он 429, без retry pool теряет основной категорийный слой.

    Returns:
        nm_to_meta: mapping nm_id -> {"rank": int, "feedbacks": int}
        dropped: number of carto dropped because feedbacks < threshold
    """
    records: list[tuple[int, int | None, int]] = []
    for attempt in range(max_retries + 1):
        records = await fetch_serp_with_feedbacks(query=subject_name, top_n=top_n)
        if records:
            break
        if attempt < max_retries:
            _log.warning(
                "serp_main_empty_retrying",
                query=subject_name,
                attempt=attempt + 1,
                delay_sec=retry_delay_sec,
            )
            await asyncio.sleep(retry_delay_sec)
    nm_to_meta: dict[int, dict[str, Any]] = {}
    dropped = 0
    for nm, fbs, rank in records:
        if fbs is None or fbs < feedbacks_threshold:
            dropped += 1
            continue
        if nm not in nm_to_meta:
            nm_to_meta[nm] = {"rank": rank, "feedbacks": fbs}
    return nm_to_meta, dropped


async def fetch_serp_narrow_layer(
    *,
    narrow_kws: list[str],
    top_n_per_kw: int,
    feedbacks_threshold: int,
    concurrency: int = 5,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Layer B — fetch top-N SERP for each narrow keyword.

    For each narrow kw, runs ``fetch_serp_with_feedbacks(query=kw, top_n=...)``,
    captures the min-rank across keywords if a nm appears in several.

    Returns:
        nm_to_meta: mapping nm_id -> {"min_rank": int, "feedbacks": int, "kws": list[str]}
        dropped: number of carto dropped because feedbacks < threshold (counted
            per occurrence across kws, so it may exceed unique nm count)
    """
    if not narrow_kws:
        return {}, 0

    sem = asyncio.Semaphore(concurrency)

    async def _fetch(kw: str) -> tuple[str, list[tuple[int, int | None, int]]]:
        async with sem:
            try:
                rows = await fetch_serp_with_feedbacks(query=kw, top_n=top_n_per_kw)
                return kw, rows
            except Exception as exc:
                _log.warning("narrow_serp_failed", kw=kw, error=repr(exc))
                return kw, []

    results = await asyncio.gather(*(_fetch(kw) for kw in narrow_kws))

    nm_to_meta: dict[int, dict[str, Any]] = {}
    dropped = 0
    for kw, rows in results:
        for nm, fbs, rank in rows:
            if fbs is None or fbs < feedbacks_threshold:
                dropped += 1
                continue
            entry = nm_to_meta.setdefault(
                nm, {"min_rank": rank, "feedbacks": fbs, "kws": []}
            )
            entry["min_rank"] = min(entry["min_rank"], rank)
            entry["feedbacks"] = max(entry["feedbacks"], fbs)
            if kw not in entry["kws"]:
                entry["kws"].append(kw)
    return nm_to_meta, dropped


async def fetch_mpstats_layer(
    *,
    token: str | None,
    subject_id: int,
    parent_category: str,
    per_window: int,
    feedbacks_threshold: int,
    period_start: date,
    period_end: date,
) -> tuple[dict[int, dict[str, Any]], int]:
    """Layer C — fetch MPStats top-N revenue across 3 windows (live, june, year).

    Each window queries MPStats /category/items independently. Items with
    ``comments < feedbacks_threshold`` are dropped (comments is the MPStats
    counterpart to WB feedbacks count).

    Returns:
        nm_to_meta: mapping nm_id -> {"revenue_max_rub": float, "comments": int,
                                      "windows": list[str]}
        dropped: count of carto excluded by the comments filter (per occurrence)
    """
    if token is None:
        _log.warning("mpstats_layer_skipped_no_token")
        return {}, 0

    windows: list[tuple[str, date, date]] = [
        ("mpstats_live", period_start, period_end),
        ("mpstats_june", _JUNE_2025_START, _JUNE_2025_END),
        ("mpstats_year", _YEAR_2025_START, _YEAR_2025_END),
    ]

    async def _fetch(
        label: str, ws: date, we: date
    ) -> tuple[str, list[tuple[int, float, int]]]:
        try:
            data = await fetch_mpstats_top_n_with_comments(
                token=token,
                subject_id=subject_id,
                parent_category=parent_category,
                period_start=ws,
                period_end=we,
                top_n=per_window,
            )
            return label, data
        except Exception as exc:
            _log.warning("mpstats_window_failed", label=label, error=repr(exc))
            return label, []

    results = await asyncio.gather(*(_fetch(lbl, ws, we) for lbl, ws, we in windows))

    nm_to_meta: dict[int, dict[str, Any]] = {}
    dropped = 0
    for label, rows in results:
        for nm, revenue, comments in rows:
            if comments < feedbacks_threshold:
                dropped += 1
                continue
            entry = nm_to_meta.setdefault(
                nm, {"revenue_max_rub": 0.0, "comments": 0, "windows": []}
            )
            entry["revenue_max_rub"] = max(entry["revenue_max_rub"], revenue)
            entry["comments"] = max(entry["comments"], comments)
            if label not in entry["windows"]:
                entry["windows"].append(label)
    return nm_to_meta, dropped


async def run_algo_i(
    *,
    engine: AsyncEngine,
    config: AlgorithmIConfig,
) -> AlgorithmIResult:
    """Run Algorithm I end-to-end. Returns AlgorithmIResult."""
    start = time.monotonic()
    today = date.today()
    yesterday = today - timedelta(days=1)
    period_end = yesterday
    period_start = yesterday - timedelta(days=config.period_days)

    # Phase 1: my top-N (used only as seed for narrow keyword extraction)
    my_nm_ids = await _resolve_my_top_nms(
        engine=engine,
        config=config,
        period_start=period_start,
        period_end=period_end,
    )
    narrow_kws = await _build_narrow_kws(
        engine=engine,
        nm_ids=my_nm_ids,
        period_start=period_start,
        period_end=period_end,
    ) if my_nm_ids else []

    # Phase 2: launch 3 layers in parallel (each independent)
    settings = get_settings()
    mpstats_token: str | None = None
    if settings.mpstats_api_token is not None:
        mpstats_token = settings.mpstats_api_token.get_secret_value()

    # Layer A first (1 SERP request) — runs solo, не конкурирует за throttle.
    (a_map, a_dropped) = await fetch_serp_main_layer(
        subject_name=config.subject_name,
        top_n=config.serp_top,
        feedbacks_threshold=config.feedbacks_threshold,
    )
    # Then Layers B + C in parallel: MPStats is a different host, SERP narrow
    # uses curl_cffi pool with Semaphore(5) internally.
    layer_b_task = asyncio.create_task(
        fetch_serp_narrow_layer(
            narrow_kws=narrow_kws,
            top_n_per_kw=config.serp_narrow_top,
            feedbacks_threshold=config.feedbacks_threshold,
        )
    )
    layer_c_task = asyncio.create_task(
        fetch_mpstats_layer(
            token=mpstats_token,
            subject_id=config.subject_id,
            parent_category=config.mpstats_parent_category,
            per_window=config.mpstats_per_window,
            feedbacks_threshold=config.feedbacks_threshold,
            period_start=period_start,
            period_end=period_end,
        )
    )
    (b_map, b_dropped) = await layer_b_task
    (c_map, c_dropped) = await layer_c_task

    # Phase 3: union with per-layer membership tracking
    layer_membership: dict[int, list[str]] = {}
    for nm in a_map:
        layer_membership.setdefault(nm, []).append("serp_main")
    for nm in b_map:
        layer_membership.setdefault(nm, []).append("serp_narrow")
    for nm in c_map:
        layer_membership.setdefault(nm, []).append("mpstats")

    candidate_nm_ids = list(layer_membership.keys())

    # Phase 4: CardDetail filter — drop nm that are missing on WB (deleted)
    details: dict[int, CardDetail] = {}
    async with CardDetailFetcher() as fetcher:
        for i in range(0, len(candidate_nm_ids), 100):
            batch = candidate_nm_ids[i : i + 100]
            try:
                results = await fetcher.fetch_batch(batch)
                for d in results:
                    details[d.nm_id] = d
            except Exception as exc:
                _log.warning("card_detail_batch_failed", error=repr(exc))

    dropped_deleted = 0
    final_pool: list[int] = []
    for nm in candidate_nm_ids:
        if nm in details:
            final_pool.append(nm)
        else:
            dropped_deleted += 1

    return AlgorithmIResult(
        pool_nm_ids=final_pool,
        feedbacks_threshold_used=config.feedbacks_threshold,
        layer_a_serp_main_count=len(a_map),
        layer_b_serp_narrow_count=len(b_map),
        layer_c_mpstats_count=len(c_map),
        narrow_kws_used=narrow_kws,
        layer_membership=layer_membership,
        dropped_low_feedbacks_per_layer={
            "serp_main": a_dropped,
            "serp_narrow": b_dropped,
            "mpstats": c_dropped,
        },
        dropped_deleted_on_wb=dropped_deleted,
        duration_seconds=time.monotonic() - start,
    )


async def _resolve_my_top_nms(
    *,
    engine: AsyncEngine,
    config: AlgorithmIConfig,
    period_start: date,
    period_end: date,
) -> list[int]:
    """Resolve my top-N carto, via --manual-top-nm-ids or sales-funnel API."""
    if config.manual_top_nm_ids:
        return list(config.manual_top_nm_ids)
    _ = engine
    settings = get_settings()
    if settings.wb_api_token is None:
        _log.warning("wb_api_token_unset_skipping_my_carto")
        return []
    token = settings.wb_api_token.get_secret_value()
    async with WBAPIClient.analytics(token=token) as client:
        fetcher = SalesFunnelFetcher(client)
        products = await fetcher.fetch_subject_products(
            subject_id=config.subject_id,
            period_start=period_start,
            period_end=period_end,
        )
    products_sorted = sorted(products, key=lambda p: (-p.order_sum_kopeks, -p.nm_id))
    return [p.nm_id for p in products_sorted[: config.n_my_cards]]


async def _build_narrow_kws(
    *,
    engine: AsyncEngine,
    nm_ids: list[int],
    period_start: date,
    period_end: date,
) -> list[str]:
    """For each nm pick one narrow keyword; dedup preserving order."""
    period_start_epoch = int(
        datetime.combine(period_start, datetime.min.time(), tzinfo=UTC).timestamp()
    )
    period_end_epoch = int(
        datetime.combine(period_end, datetime.min.time(), tzinfo=UTC).timestamp()
    )
    narrow_kws: list[str] = []
    seen: set[str] = set()
    for nm in nm_ids:
        async with UnitOfWork(engine) as uow:
            title_row = await uow.session.execute(
                text("SELECT title FROM wb_cards WHERE nm_id = :nm"),
                {"nm": nm},
            )
            title = title_row.scalar_one_or_none()
            if title is None:
                continue
            kw_rows = await uow.session.execute(
                text(
                    "SELECT keyword, AVG(traffic) AS tr "
                    "FROM mpstats_keywords "
                    "WHERE nm_id = :nm AND date >= :ps AND date <= :pe "
                    "  AND avg_position > 0 AND avg_position <= 20 "
                    "  AND traffic > 0 "
                    "GROUP BY keyword ORDER BY tr DESC"
                ),
                {
                    "nm": nm,
                    "ps": period_start_epoch,
                    "pe": period_end_epoch,
                },
            )
            candidates = [(str(r[0]), float(r[1])) for r in kw_rows]
        kw = pick_narrow_keyword(
            title=title,
            candidates=candidates,
            brand_markers=_BRAND_MARKERS,
        )
        if kw is None or kw in seen:
            continue
        seen.add(kw)
        narrow_kws.append(kw)
    return narrow_kws


__all__ = [
    "fetch_mpstats_layer",
    "fetch_serp_main_layer",
    "fetch_serp_narrow_layer",
    "run_algo_i",
]
