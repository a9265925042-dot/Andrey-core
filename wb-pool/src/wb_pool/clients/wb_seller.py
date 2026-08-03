"""WB Seller API HTTP client (Bearer JWT)."""

from __future__ import annotations

from types import TracebackType
from typing import Any, ClassVar, Self

import httpx


class BaseAPIClient:
    def __init__(self, *, base_url: str, token: str, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": token,
                "Content-Type": "application/json",
            },
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        r = await self._client.post(path, **kwargs)
        r.raise_for_status()
        return r

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        r = await self._client.get(path, **kwargs)
        r.raise_for_status()
        return r


class WBAPIClient(BaseAPIClient):
    BASES: ClassVar[dict[str, str]] = {
        "content": "https://content-api.wildberries.ru",
        "statistics": "https://statistics-api.wildberries.ru",
        "analytics": "https://seller-content.wildberries.ru",
    }

    @classmethod
    def content(cls, token: str, **kw: Any) -> WBAPIClient:
        return cls(base_url=cls.BASES["content"], token=token, **kw)

    @classmethod
    def statistics(cls, token: str, **kw: Any) -> WBAPIClient:
        return cls(base_url=cls.BASES["statistics"], token=token, **kw)

    @classmethod
    def analytics(cls, token: str, **kw: Any) -> WBAPIClient:
        return cls(base_url=cls.BASES["analytics"], token=token, **kw)


__all__ = ["BaseAPIClient", "WBAPIClient"]
