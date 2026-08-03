"""Sales-funnel fetcher — ranking моих карточек по revenue (Jam tier).

POST /api/analytics/v3/sales-funnel/products доступен только на Jam tier и
выше. Если Jam недоступен (403/404) — используй `--manual-top-nm-ids` в
discover CLI (см. docs/09-troubleshooting.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from wb_pool.clients.wb_seller import WBAPIClient


@dataclass(frozen=True)
class ProductStat:
    nm_id: int
    title: str
    order_sum_kopeks: int

    @classmethod
    def from_raw(cls, p: dict[str, Any]) -> ProductStat:
        return cls(
            nm_id=int(p["product"]["nmId"]),
            title=str(p["product"].get("title", "")),
            order_sum_kopeks=round(float(p["statistic"]["selected"]["orderSum"]) * 100),
        )


class SalesFunnelFetcher:
    def __init__(self, client: WBAPIClient):
        self._client = client

    async def fetch_subject_products(
        self, *, subject_id: int, period_start: date, period_end: date
    ) -> list[ProductStat]:
        r = await self._client.post(
            "/api/analytics/v3/sales-funnel/products",
            json={
                "period": {
                    "start": period_start.isoformat(),
                    "end": period_end.isoformat(),
                },
                "subjectIds": [subject_id],
            },
        )
        products = r.json().get("data", {}).get("products", [])
        return [ProductStat.from_raw(p) for p in products]


__all__ = ["ProductStat", "SalesFunnelFetcher"]
