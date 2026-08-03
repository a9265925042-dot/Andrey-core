"""WB SERP клиент через curl_cffi (TLS-fingerprint chrome116).

Version cascade: v18 → v17 → v15 → v12 → v9. WB периодически меняет endpoint
URLs и payload format — при live 429/5xx каскад пробует следующую версию.

⚠ WB SERP throttle — global per-IP. Не запускай >5 discovery/час.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, ClassVar

from curl_cffi.requests import AsyncSession

from wb_pool.logging_setup import get_logger

_log = get_logger(__name__)


class SERPClient:
    VERSIONS: ClassVar[list[str]] = ["v18", "v17", "v15", "v12", "v9"]
    BASE = "https://u-search.wb.ru/exactmatch/ru/common/{version}/search"

    def __init__(self, impersonate: str = "chrome116", timeout: float = 15.0):
        self._impersonate = impersonate
        self._timeout = timeout
        self._session: AsyncSession[Any] | None = None

    async def __aenter__(self) -> SERPClient:
        self._session = AsyncSession(
            impersonate=self._impersonate, timeout=self._timeout
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def search_with_version_cascade(
        self, *, query: str, page: int = 1
    ) -> dict[str, Any]:
        """GET SERP for query/page; try each API version until one succeeds."""
        if self._session is None:
            raise RuntimeError("SERPClient must be used as async context manager")
        params: dict[str, Any] = {
            "query": query,
            "resultset": "catalog",
            "page": page,
            "dest": -1257786,
            "appType": 1,
            "curr": "rub",
            "lang": "ru",
        }
        last_exc: Exception | None = None
        for version in self.VERSIONS:
            url = self.BASE.format(version=version)
            try:
                r = await self._session.get(url, params=params)
                r.raise_for_status()
                payload: dict[str, Any] = r.json()
                return payload
            except Exception as exc:
                last_exc = exc
                _log.debug(
                    "serp_version_failed", version=version, page=page, error=repr(exc)
                )
                continue
        assert last_exc is not None
        raise last_exc


__all__ = ["SERPClient"]
