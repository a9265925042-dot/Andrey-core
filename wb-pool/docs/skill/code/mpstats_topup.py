"""MPStats topup — adds top-by-revenue articles to the v2 pool.

The v2 pool builder (:func:`build_category_pool_v2`) gets recall@30 ≈ 83%
on the shampoo benchmark from SERP/RRF signals alone. Adding MPStats
top-revenue articles for the same subject lifts it to ≈ 90% (validated
live, see ``data/research/2026-05-17-shampoo-signals``).

The mechanism is intentionally minimal:

* Two ``POST /category/items`` calls — one current 30-day window, one
  fixed June-2025 reference window.
* Dedup'd union (current first, then june uniques) is the *top-up*
  list.
* :func:`merge_pool_with_topup` is the pure helper that joins the
  builder's RRF result with the top-up; it rounds the final size *down*
  to the nearest multiple of ``round_up_to`` (default 5), which gives
  the cabinet-comparison job sizing knob a clean denominator (groups of
  5 articles are the WB cabinet's natural quantum).

API gotchas, learned the hard way during live testing — both encoded
in the test suite and worth repeating here:

* ``d2`` (window end) must be ``≤ today - 1``. ``d2 = date.today()``
  returns HTTP 422 from MPStats.
* The ``path`` URL param is the **parent category name** (e.g.
  ``"Красота"``), not the subject path. The ``filterModel`` then
  narrows to a specific ``subject_id``.
* The body uses AG-grid's ``filterModel`` shape:
  ``{"<col>": {"filterType": "number", "type": "equals", "filter": N}}``.
* The response shape is ``{"data": [{"id": <nm_id>, ...}, ...], "total": N}``
  where ``id`` is the WB nm_id (NOT ``itemid``, which is MPStats
  internal).
* MPStats budget caveat: each :func:`fetch_mpstats_topup` call costs
  exactly 2 of the 150 monthly operations. Caller is responsible for
  the global budget — see ``mpstats_canary`` for the live remaining
  count.
"""

from __future__ import annotations

import asyncio
import json as _json
from datetime import date, timedelta
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from wb_pool.models import MPStatsListingRow

MPSTATS_BASE = "https://mpstats.io/api/analytics/v1/wb"
CATEGORY_PATH = "/category/items"


async def fetch_mpstats_top_n_in_window(
    *,
    token: str,
    subject_id: int,
    parent_category: str,
    period_start: date,
    period_end: date,
    top_n: int,
) -> list[int]:
    """Fetch top-N nm_ids from MPStats /category/items for ONE window.

    Sorted by revenue DESC. Building block — callers compose multiple
    windows / merging as needed. ``period_start``/``period_end`` map to
    MPStats wire-format ``d1``/``d2`` respectively (inclusive bounds).

    :raises httpx.HTTPStatusError: non-2xx from MPStats.
    """
    headers = {
        "X-Mpstats-TOKEN": token,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "filterModel": {
            "subject_id": {
                "filterType": "number",
                "type": "equals",
                "filter": subject_id,
            }
        },
        "sortModel": [{"colId": "revenue", "sort": "desc"}],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{MPSTATS_BASE}{CATEGORY_PATH}",
            params={
                "path": parent_category,
                "d1": period_start.isoformat(),
                "d2": period_end.isoformat(),
                "startRow": 0,
                "endRow": top_n,
            },
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json()
        return [int(item["id"]) for item in data.get("data", [])]


async def fetch_mpstats_top_n_with_comments(
    *,
    token: str,
    subject_id: int,
    parent_category: str,
    period_start: date,
    period_end: date,
    top_n: int,
) -> list[tuple[int, float, int]]:
    """Fetch top-N items from MPStats /category/items with revenue + comments.

    Same endpoint shape as :func:`fetch_mpstats_top_n_in_window` but returns
    ``(nm_id, revenue_rub, comments)`` tuples — needed by Algorithm H for the
    review-gate filter applied at fetch time.

    Sort: revenue DESC. ``comments`` field is MPStats' counterpart to
    WB's ``feedbacks``. None values coerce to 0 (cards without reviews —
    won't pass any positive threshold).

    :raises httpx.HTTPStatusError: non-2xx from MPStats (e.g. 422 on too
        long ``period_end - period_start``; caller catches and may skip
        the window).
    """
    headers = {
        "X-Mpstats-TOKEN": token,
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {
        "filterModel": {
            "subject_id": {
                "filterType": "number",
                "type": "equals",
                "filter": subject_id,
            }
        },
        "sortModel": [{"colId": "revenue", "sort": "desc"}],
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{MPSTATS_BASE}{CATEGORY_PATH}",
            params={
                "path": parent_category,
                "d1": period_start.isoformat(),
                "d2": period_end.isoformat(),
                "startRow": 0,
                "endRow": top_n,
            },
            headers=headers,
            json=body,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        out: list[tuple[int, float, int]] = []
        for item in data:
            nm = int(item["id"])
            rev = float(item.get("revenue") or 0)
            comments_raw = item.get("comments")
            comments = int(comments_raw) if comments_raw is not None else 0
            out.append((nm, rev, comments))
        return out


async def fetch_mpstats_topup(
    *,
    token: str,
    subject_id: int,
    parent_category: str = "Красота",
    top_n_per_window: int = 40,
    today: date | None = None,
) -> list[int]:
    """Fetch dedup'd top-N nm_ids from MPStats current + June-2025 windows.

    Calls ``POST /category/items`` twice (in parallel via
    :func:`asyncio.gather`), once for the current rolling 30-day window
    and once for the fixed June-2025 reference window. Returns the
    concatenated nm_ids with duplicates removed *while preserving order*
    — current ranking first, then June-2025 uniques.

    Args:
        token: MPStats API token (the ``X-Mpstats-TOKEN`` header value).
            Caller is responsible for sourcing it from settings.
        subject_id: WB ``xsubject`` value (358 = Шампуни, 357 = Кремы,
            …). Sent as the ``subject_id`` filter in the AG-grid
            ``filterModel`` block.
        parent_category: WB top-level category name for MPStats' ``path``
            URL param. Examples: ``"Красота"`` for any cosmetic subject,
            ``"Одежда"`` for clothing, etc. Validate per-category at
            integration time — the API rejects unknown paths.
        top_n_per_window: How many top articles to fetch per window.
            Default 40. Each call requests ``startRow=0,endRow=top_n_per_window``.
        today: Override "today" for testing. Default = :func:`date.today`.
            **Note** — MPStats rejects ``d2 = today`` with HTTP 422; the
            function uses ``today - 1`` as the current window's d2 to
            stay safe.

    Returns:
        Ordered list of nm_ids. Up to ``2 * top_n_per_window`` after
        dedup. Empty list if both calls return empty ``data``.

    Raises:
        httpx.HTTPStatusError: If either MPStats call returns a non-2xx.
            Caller decides whether to retry or surface the error.
    """
    if today is None:
        today = date.today()

    # Current window: 30 days ending yesterday (d2 must be ≤ today - 1).
    curr_d2 = today - timedelta(days=1)
    curr_d1 = curr_d2 - timedelta(days=30)

    # June 2025 fixed reference window.
    june_d1 = date(2025, 6, 1)
    june_d2 = date(2025, 6, 30)

    curr_nm, june_nm = await asyncio.gather(
        fetch_mpstats_top_n_in_window(
            token=token,
            subject_id=subject_id,
            parent_category=parent_category,
            period_start=curr_d1,
            period_end=curr_d2,
            top_n=top_n_per_window,
        ),
        fetch_mpstats_top_n_in_window(
            token=token,
            subject_id=subject_id,
            parent_category=parent_category,
            period_start=june_d1,
            period_end=june_d2,
            top_n=top_n_per_window,
        ),
    )

    # Preserve order, dedup.
    seen: set[int] = set()
    result: list[int] = []
    for nm in [*curr_nm, *june_nm]:
        if nm not in seen:
            seen.add(nm)
            result.append(nm)
    return result


def merge_pool_with_topup(
    pool: list[int],
    topup: list[int],
    *,
    target_size: int = 100,
    round_up_to: int = 5,
) -> list[int]:
    """Append unique topup items to pool, pad up to nearest multiple of ``round_up_to``.

    Round-UP (ceiling) matches cabinet purchase quantum: WB cabinet groups
    сравнения in batches of 5 nm — if you end up at 127 unique nm, cabinet
    charges 27 slots x 5 = 135 anyway, so pad to 135 with extra topup.
    If unique_topup wasn't deep enough to reach the next multiple, the
    result truncates to the largest multiple ≤ available candidates.

    Args:
        pool: The base ranking from :func:`build_category_pool_v2`'s RRF
            ensemble. Order is preserved verbatim — these go first.
        topup: Candidates from :func:`fetch_mpstats_topup` or
            :func:`load_baseline_topup`. Order preserved after dedup.
        target_size: Hint for caller's intended pool floor. Documentation only.
        round_up_to: Pad up to nearest multiple of this. Default 5 (cabinet
            quantum). Pass 1 to disable rounding.

    Returns:
        ``pool`` followed by unique topup, padded UP to a multiple of
        ``round_up_to`` when topup is deep enough; otherwise truncated
        down to the largest multiple that fits within available candidates.

    Examples:
        >>> # 10 pool + 5 unique = 15 (multiple of 5) → 15
        >>> merge_pool_with_topup(list(range(1, 11)), [20, 30, 40, 50, 60])
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 30, 40, 50, 60]
        >>> # 10 pool + 3 unique = 13 → round UP to 15 (but only 3 in topup → fits 10)
        >>> merge_pool_with_topup(list(range(1, 11)), [20, 30, 40])
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
        >>> # 10 pool + 7 unique = 17 → round UP to 20 (need 3 more)
        >>> merge_pool_with_topup(list(range(1, 11)), list(range(20, 27)))
        [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 20, 21, 22, 23, 24]
    """
    _ = target_size  # Documentation hint — see Args.
    pool_set = set(pool)
    unique_topup = [nm for nm in topup if nm not in pool_set]
    result = [*pool, *unique_topup]
    # Round UP (ceiling) to nearest multiple, bounded by available candidates.
    target_ceil = ((len(result) + round_up_to - 1) // round_up_to) * round_up_to
    final_size = min(target_ceil, len(result))
    # If we couldn't reach the ceil (topup too shallow), truncate to largest
    # multiple that fits — better than returning a non-multiple length.
    if final_size != target_ceil:
        final_size = (len(result) // round_up_to) * round_up_to
    return result[:final_size]


async def load_baseline_topup(
    session: AsyncSession,
    subject_id: int,
    *,
    top_n_per_window: int = 40,
) -> list[int]:
    """Read MPStats baseline rows for subject from DB → ordered dedup'd nm_id list.

    ⚠️ Returns ONLY nm_ids — never numeric fields. MPStats baseline rows
    store revenue_kopeks/sales/balance as NULL by design
    (decision-mpstats-trust-revised + safety-mpstats-baseline-numbers-untrusted).
    Function signature returns list[int] specifically to prevent accidental
    consumption of synthetic numbers.

    Selection: source_type='category' AND source_query='subject:N'.
    Ordering: window with smaller period (june — 30 days) first by rank;
    window with larger period (full_year — 365 days) fills after by rank.
    Каждое окно ограничено top_n_per_window. Возвращает dedup'd list.

    Empty list → caller should fallback to live fetch_mpstats_topup.
    """
    stmt = select(
        MPStatsListingRow.nm_id,
        MPStatsListingRow.period_start,
        MPStatsListingRow.period_end,
        MPStatsListingRow.extra_json,
    ).where(
        MPStatsListingRow.source_type == "category",
        MPStatsListingRow.source_query == f"subject:{subject_id}",
    )
    rows = (await session.execute(stmt)).all()
    if not rows:
        return []

    # Group by (period_start, period_end) — shorter window first
    groups: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for row in rows:
        try:
            rank = int(_json.loads(row.extra_json or "{}").get("rank", 999999))
        except (ValueError, TypeError):
            rank = 999999
        groups.setdefault((row.period_start, row.period_end), []).append(
            (rank, row.nm_id)
        )

    # Sort partitions by period length ascending (shorter = more focused first)
    sorted_partitions = sorted(groups.keys(), key=lambda k: k[1] - k[0])

    result: list[int] = []
    seen: set[int] = set()
    for partition in sorted_partitions:
        ranked = sorted(groups[partition])[:top_n_per_window]
        for _rank, nm_id in ranked:
            if nm_id not in seen:
                seen.add(nm_id)
                result.append(nm_id)
    return result


__all__ = [
    "CATEGORY_PATH",
    "MPSTATS_BASE",
    "fetch_mpstats_top_n_in_window",
    "fetch_mpstats_top_n_with_comments",
    "fetch_mpstats_topup",
    "load_baseline_topup",
    "merge_pool_with_topup",
]
