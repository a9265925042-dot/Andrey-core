"""SERP helper that preserves `feedbacks` from response.

Existing :class:`wbap.competitor_pool.serp_source.CurlCffiSerpSource.fetch_nm_list`
returns only ``list[int]`` of nm_ids. Algorithm H needs feedbacks for the
review-gate filter; this helper paginates SERP and returns triples.

Per-page failures (HTTP/JSON errors) are caught and logged - caller
receives collected results up to the failure point. This mirrors the
resilience convention from the experiments module.
"""

from __future__ import annotations

import math
from typing import Any

from wb_pool.clients.serp import SERPClient
from wb_pool.logging_setup import get_logger

_log = get_logger(__name__)


async def fetch_serp_with_feedbacks(
    *,
    query: str,
    top_n: int,
    impersonate: str = "chrome116",
    timeout: float = 15.0,
) -> list[tuple[int, int | None, int]]:
    """Fetch SERP for ``query``, return list of (nm_id, feedbacks, rank).

    Rank is 1-based position in the cumulative paginated results.
    Feedbacks is the integer count from ``SerpProduct.feedbacks`` (may be None
    for very new cards without reviews).

    Paginates from page 1 to ``ceil(top_n / 100)``. Stops early on empty page.
    Per-page exception -> log warning + break (return collected so far).
    """
    if top_n <= 0:
        return []
    pages_needed = math.ceil(top_n / 100)
    out: list[tuple[int, int | None, int]] = []
    rank = 0

    async with SERPClient(impersonate=impersonate, timeout=timeout) as client:
        for page in range(1, pages_needed + 1):
            try:
                payload: dict[str, Any] = await client.search_with_version_cascade(
                    query=query, page=page
                )
            except Exception as exc:
                _log.warning(
                    "serp_fetch_failed_resilient",
                    query=query,
                    page=page,
                    error=repr(exc),
                )
                break
            products = (
                payload.get("products")
                or (payload.get("data") or {}).get("products")
                or []
            )
            if not products:
                break
            for p in products:
                if "id" not in p:
                    continue
                rank += 1
                fbs_raw = p.get("feedbacks")
                fbs = int(fbs_raw) if fbs_raw is not None else None
                out.append((int(p["id"]), fbs, rank))
                if rank >= top_n:
                    return out
    return out


__all__ = ["fetch_serp_with_feedbacks"]
