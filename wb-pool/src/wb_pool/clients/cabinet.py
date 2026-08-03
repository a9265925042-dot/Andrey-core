"""WB Cabinet HTTP клиент — сессионные cookies (не Bearer JWT).

Cabinet endpoints живут под seller-content.wildberries.ru/ns/analytics-api/...
и авторизуются HttpOnly session cookies, импортированными из Chrome
(см. clients/cookies.py). Cookies живут ~1-2 недели.
"""

from __future__ import annotations

from http.cookiejar import Cookie
from types import TracebackType
from typing import Any

import httpx

LIMITS_PATH = (
    "/ns/analytics-api/content-analytics/api/v2/competitor-comparison/limits"
)
COMPARISON_NMS_PATH = (
    "/ns/analytics-api/content-analytics/api/v2/competitor-comparison/nms"
)
FILE_MANAGER_DOWNLOAD_PATH = (
    "/ns/analytics-api/content-analytics/api/v1/file-manager/download"
)
FILE_MANAGER_DOWNLOADS_PATH = (
    "/ns/analytics-api/content-analytics/api/v1/file-manager/downloads"
)


class CabinetAuthExpired(Exception):
    """Cookies expired или невалидные — re-run `wb-pool setup-cookies`."""


class WBCabinetClient:
    BASE = "https://seller-content.wildberries.ru"

    def __init__(self, cookies: list[Cookie], *, timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=self.BASE,
            cookies={c.name: c.value or "" for c in cookies},
            timeout=timeout,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/116.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json",
            },
        )

    async def __aenter__(self) -> WBCabinetClient:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._client.aclose()

    async def post(self, path: str, **kwargs: Any) -> dict[str, Any]:
        r = await self._client.post(path, **kwargs)
        if r.status_code == 401:
            raise CabinetAuthExpired("Cookies expired, re-run setup-cookies")
        r.raise_for_status()
        payload: dict[str, Any] = r.json()
        return payload

    async def get(self, path: str, **kwargs: Any) -> dict[str, Any]:
        r = await self._client.get(path, **kwargs)
        if r.status_code == 401:
            raise CabinetAuthExpired("Cookies expired, re-run setup-cookies")
        r.raise_for_status()
        payload: dict[str, Any] = r.json()
        return payload

    async def limits(self) -> dict[str, Any]:
        """GET comparison limits — {"available": N, "used": M, ...}."""
        return await self.get(LIMITS_PATH)


__all__ = [
    "COMPARISON_NMS_PATH",
    "FILE_MANAGER_DOWNLOADS_PATH",
    "FILE_MANAGER_DOWNLOAD_PATH",
    "LIMITS_PATH",
    "CabinetAuthExpired",
    "WBCabinetClient",
]
