"""Public WB CardDetail batch fetcher — фильтр удалённых карточек."""

from __future__ import annotations

from dataclasses import dataclass
from types import TracebackType

import httpx


@dataclass(frozen=True)
class CardDetail:
    nm_id: int
    brand: str
    name: str
    feedbacks: int
    rating: float


class CardDetailFetcher:
    URL = "https://card.wb.ru/cards/detail"
    MAX_BATCH = 100

    def __init__(self, *, timeout: float = 15.0):
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> CardDetailFetcher:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch_batch(self, nm_ids: list[int]) -> list[CardDetail]:
        if self._client is None:
            raise RuntimeError("CardDetailFetcher must be used as async context manager")
        nm_str = ";".join(map(str, nm_ids[: self.MAX_BATCH]))
        r = await self._client.get(
            self.URL,
            params={"appType": "1", "curr": "rub", "nm": nm_str, "dest": -1257786},
        )
        r.raise_for_status()
        products = r.json().get("data", {}).get("products", [])
        return [
            CardDetail(
                nm_id=int(p["id"]),
                brand=str(p.get("brand", "")),
                name=str(p.get("name", "")),
                feedbacks=int(p.get("feedbacks", 0)),
                rating=float(p.get("reviewRating", 0.0)),
            )
            for p in products
        ]


__all__ = ["CardDetail", "CardDetailFetcher"]
