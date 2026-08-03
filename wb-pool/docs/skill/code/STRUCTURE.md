# Code Structure — что есть и что нужно реализовать

Этот skill даёт **разработанный и работающий код Discovery layer** (4 worker'а + orchestrator). Остальные модули (cabinet HTTP клиент, ETL, cookies loader) описаны как **contracts** — друг реализует их под свою архитектуру.

Это **не** полное turn-key решение, а **architectural blueprint + reference implementation главного алгоритма**.

## Целевая структура проекта (после адаптации)

```
wb-pool/
├── src/wb_pool/
│   ├── __init__.py
│   ├── config.py                       # ⚠ STUB — Pydantic Settings
│   ├── logging_setup.py                # ⚠ STUB — structlog setup
│   ├── db.py                           # ⚠ STUB — SQLAlchemy engine + UoW
│   ├── cli.py                          # ✅ собирается из cli_*.py
│   │
│   ├── models/                         # ⚠ STUB — SQLAlchemy ORM models
│   │   ├── __init__.py
│   │   ├── raw.py                      # RawRun
│   │   ├── wb_cards.py                 # WBCard
│   │   ├── mpstats.py                  # MPStatsListingRow, MPStatsKeyword
│   │   ├── cmp_groups.py               # CmpGroup
│   │   ├── cmp_funnel.py               # CmpFunnelDaily
│   │   └── cmp_search.py               # CmpSearchQuery, CmpSearchQueryPerNm
│   │
│   ├── clients/                        # HTTP клиенты к external services
│   │   ├── __init__.py
│   │   ├── wb_seller.py                # ⚠ STUB — WBAPIClient (Bearer JWT)
│   │   ├── card_detail.py              # ⚠ STUB — public WB CardDetail batch fetcher
│   │   ├── sales_funnel.py             # ⚠ STUB — Jam-tier ranking endpoint
│   │   ├── serp.py                     # ⚠ STUB — SERP клиент через curl_cffi
│   │   ├── cabinet.py                  # ⚠ STUB — WBCabinetClient (cookies)
│   │   └── cookies.py                  # ⚠ STUB — browser_cookie3 wrapper
│   │
│   ├── discovery/                      # ✅ DISCOVERY LAYER (provided)
│   │   ├── __init__.py
│   │   ├── algo_i.py                   # ✅ provided — orchestrator + 4 workers
│   │   ├── algo_i_types.py             # ✅ provided — Config + Result
│   │   ├── title_kw_extractor.py       # ✅ provided — narrow keywords
│   │   ├── serp_with_feedbacks.py      # ✅ provided — SERP layer A+B
│   │   └── mpstats_layer.py            # ✅ provided (rename from mpstats_topup.py)
│   │
│   ├── purchase/                       # ⚠ STUB — cabinet purchase saga
│   │   ├── __init__.py
│   │   ├── groups.py                   # GroupPlan, compute_comparison_id, purchase_group
│   │   └── saga.py                     # buy loop с try/except + DLQ
│   │
│   └── etl/                            # ⚠ STUB — ETL handlers
│       ├── __init__.py
│       ├── funnel.py                   # seed_cmp_group + upsert_funnel_daily
│       └── excel.py                    # import_comparison_to_db
```

**Легенда:**
- ✅ — код предоставлен в `code/` (готов к использованию после rename + put в правильное место)
- ⚠ STUB — реализация описана как contract, друг должен написать сам

## Что предоставлено (✅)

### `discovery/algo_i.py` (главный orchestrator)

7 фаз discovery + 4 worker'а. Полностью готов. Импортирует:
- `from wb_pool.discovery.algo_i_types import ...` — Config + Result dataclasses (provided)
- `from wb_pool.discovery.serp_with_feedbacks import fetch_serp_with_feedbacks` — provided
- `from wb_pool.discovery.title_kw_extractor import pick_narrow_keyword` — provided
- `from wb_pool.discovery.mpstats_layer import fetch_mpstats_top_n_with_comments` — provided
- `from wb_pool.config import get_settings` — STUB (см. ниже)
- `from wb_pool.logging_setup import get_logger` — STUB
- `from wb_pool.db import UnitOfWork` — STUB
- `from wb_pool.clients.{card_detail,wb_seller,sales_funnel} import ...` — STUB

### `discovery/title_kw_extractor.py` (pure functions, 24 tests pass)

- `TITLE_STOPWORDS: frozenset[str]` — универсальные стопворды (подарок, набор, мл, шт, etc)
- `NARROW_KW_BLACKLIST: frozenset[str]` — generic слова (крем, шампунь, маска…)
- `tokenise_title(title, *, brand_markers) -> list[str]`
- `extract_main_noun_phrase(title, *, brand_markers, max_tokens=3) -> str`
- `pick_narrow_keyword(*, title, candidates, brand_markers, ...) -> str | None`

**Чисто pure functions**, никаких внешних зависимостей кроме `re`. Можно использовать as-is.

### `discovery/serp_with_feedbacks.py`

`fetch_serp_with_feedbacks(query, top_n, ...) -> list[(nm_id, feedbacks, rank)]`

Зависит от **STUB `clients/serp.py`** (SERPClient class).

### `discovery/mpstats_layer.py` (был mpstats_topup.py)

`fetch_mpstats_top_n_with_comments(*, token, subject_id, parent_category, period_start, period_end, top_n) -> list[(nm_id, revenue_rub, comments)]`

Pure HTTP клиент через `httpx.AsyncClient` — НЕ требует stub'ов кроме models. Можно использовать почти as-is.

⚠ Импортирует `from wb_pool.models import MPStatsListingRow` — это для функции `load_baseline_topup` которая использует БД cache. **Если** ты не реализуешь cache — удали эту функцию.

### `cli_discover.py` / `cli_buy.py` / `cli_ingest_excel.py`

Typer CLI обёртки. Зависят от orchestrator + STUB модулей.

## Что нужно реализовать самому (⚠ STUB)

### `config.py` (settings)

```python
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Tokens
    wb_api_token: SecretStr | None = Field(None, description="WB Seller API Personal JWT")
    mpstats_api_token: SecretStr | None = Field(None, description="MPStats Analytics v1")

    # Cabinet
    wb_cabinet_cookies_path: str = "./data/cabinet_cookies.pkl"

    # Database
    database_url: str = "sqlite+aiosqlite:///./data/wb-pool.db"

    # Brand markers (comma-separated)
    my_brand_markers: str = ""

    @property
    def brand_markers_set(self) -> set[str]:
        return {m.strip().lower() for m in self.my_brand_markers.split(",") if m.strip()}

    # MPStats
    mpstats_parent_category: str = "Красота"

    # Logging
    log_level: str = "INFO"
    log_json: bool = False

@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
```

### `logging_setup.py` (structlog)

```python
import logging, sys, structlog
from .config import get_settings

def _configure():
    settings = get_settings()
    level = getattr(logging, settings.log_level.upper())
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    processors = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    if settings.log_json:
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
    )

_configure()

def get_logger(name: str):
    return structlog.get_logger(name)
```

### `db.py` (SQLAlchemy engine + UoW)

```python
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

def create_engine(database_url: str, **kwargs) -> AsyncEngine:
    return create_async_engine(database_url, future=True, echo=False, **kwargs)

class UnitOfWork:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.session: AsyncSession | None = None

    async def __aenter__(self) -> "UnitOfWork":
        self.session = self._session_factory()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.session.rollback()
        await self.session.close()

    async def commit(self):
        await self.session.commit()
```

### `clients/serp.py` (curl_cffi для WB SERP)

См. WB Public SERP docs / reverse engineering. Главное — version cascade (v18 → v17 → v15 → v12 → v9 если 429), TLS-fingerprint `chrome116`.

Псевдокод:
```python
from curl_cffi.requests import AsyncSession

class SERPClient:
    VERSIONS = ["v18", "v17", "v15", "v12", "v9"]
    BASE = "https://u-search.wb.ru/exactmatch/ru/common/{version}/search"

    def __init__(self, impersonate: str = "chrome116", timeout: float = 15.0):
        self._impersonate = impersonate
        self._timeout = timeout
        self._session: AsyncSession | None = None

    async def __aenter__(self):
        self._session = AsyncSession(impersonate=self._impersonate, timeout=self._timeout)
        return self

    async def __aexit__(self, *exc):
        await self._session.close()

    async def search_with_version_cascade(self, *, query: str, page: int = 1) -> dict:
        last_exc = None
        for v in self.VERSIONS:
            url = self.BASE.format(version=v)
            params = {"query": query, "resultset": "catalog", "page": page, "dest": -1257786}
            try:
                r = await self._session.get(url, params=params)
                r.raise_for_status()
                return r.json()
            except Exception as e:
                last_exc = e
                continue
        raise last_exc  # all versions failed
```

⚠ WB SERP периодически меняет endpoint URLs и payload format. Тестируй live перед production.

### `clients/wb_seller.py` (Seller API client)

```python
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

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def post(self, path, **kwargs):
        r = await self._client.post(path, **kwargs)
        r.raise_for_status()
        return r

class WBAPIClient(BaseAPIClient):
    BASES = {
        "content": "https://content-api.wildberries.ru",
        "statistics": "https://statistics-api.wildberries.ru",
        "analytics": "https://seller-content.wildberries.ru",
    }
    @classmethod
    def content(cls, token, **kw): return cls(base_url=cls.BASES["content"], token=token, **kw)
    @classmethod
    def analytics(cls, token, **kw): return cls(base_url=cls.BASES["analytics"], token=token, **kw)
```

### `clients/sales_funnel.py` (Jam-tier ranking)

```python
from dataclasses import dataclass
from datetime import date
from .wb_seller import WBAPIClient

@dataclass(frozen=True)
class ProductStat:
    nm_id: int
    title: str
    order_sum_kopeks: int

    @classmethod
    def from_raw(cls, p):
        return cls(
            nm_id=int(p["product"]["nmId"]),
            title=p["product"].get("title", ""),
            order_sum_kopeks=int(p["statistic"]["selected"]["orderSum"] * 100),
        )

class SalesFunnelFetcher:
    def __init__(self, client: WBAPIClient):
        self._client = client

    async def fetch_subject_products(
        self, *, subject_id: int, period_start: date, period_end: date
    ) -> list[ProductStat]:
        # POST /api/analytics/v3/sales-funnel/products
        r = await self._client.post(
            "/api/analytics/v3/sales-funnel/products",
            json={
                "period": {"start": period_start.isoformat(), "end": period_end.isoformat()},
                "subjectIds": [subject_id],
            },
        )
        return [ProductStat.from_raw(p) for p in r.json()["data"]["products"]]
```

### `clients/card_detail.py` (Public batch)

```python
import httpx
from dataclasses import dataclass

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

    def __init__(self, *, timeout=15.0):
        self._timeout = timeout

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def fetch_batch(self, nm_ids: list[int]) -> list[CardDetail]:
        nm_str = ";".join(map(str, nm_ids[:self.MAX_BATCH]))
        r = await self._client.get(self.URL, params={"appType": "1", "nm": nm_str, "dest": -1257786})
        r.raise_for_status()
        products = r.json().get("data", {}).get("products", [])
        return [
            CardDetail(
                nm_id=int(p["id"]),
                brand=p.get("brand", ""),
                name=p.get("name", ""),
                feedbacks=int(p.get("feedbacks", 0)),
                rating=float(p.get("reviewRating", 0.0)),
            )
            for p in products
        ]
```

### `clients/cabinet.py` (Cabinet HTTP client с cookies)

```python
import httpx

class CabinetAuthExpired(Exception): pass

class WBCabinetClient:
    BASE = "https://seller-content.wildberries.ru"

    def __init__(self, cookies, *, timeout=30.0):
        self._client = httpx.AsyncClient(
            base_url=self.BASE,
            cookies={c.name: c.value for c in cookies},
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
                "Accept": "application/json",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def post(self, path: str, **kw):
        r = await self._client.post(path, **kw)
        if r.status_code == 401:
            raise CabinetAuthExpired("Cookies expired, re-run setup-cookies")
        r.raise_for_status()
        return r.json()

    async def get(self, path: str, **kw):
        r = await self._client.get(path, **kw)
        if r.status_code == 401:
            raise CabinetAuthExpired()
        r.raise_for_status()
        return r.json()
```

### `clients/cookies.py` (browser_cookie3 wrapper)

```python
import browser_cookie3
import pickle
from pathlib import Path

def import_chrome_cookies(*, output_path: Path, cookie_file: Path | None = None) -> int:
    """
    Returns: count of imported cookies.
    """
    cj = browser_cookie3.chrome(cookie_file=str(cookie_file) if cookie_file else None,
                                 domain_name="wildberries.ru")
    relevant = [c for c in cj if "wildberries.ru" in c.domain]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as f:
        pickle.dump(relevant, f)
    return len(relevant)

def load_cookies(path: Path | None = None):
    p = path or Path("data/cabinet_cookies.pkl")
    if not p.exists():
        raise FileNotFoundError(f"Cookies not imported. Run: wb-pool setup-cookies")
    with p.open("rb") as f:
        return pickle.load(f)
```

### `purchase/groups.py` (purchase saga primitives)

```python
import hashlib
import json
from dataclasses import dataclass
from datetime import date

from ..clients.cabinet import WBCabinetClient

@dataclass(frozen=True)
class GroupPlan:
    nm_ids: list[int]

def compute_comparison_id(*, subject_id: int, plan: GroupPlan, period_start: date, period_end: date) -> str:
    """Deterministic ID для idempotent purchase."""
    nm_sorted = sorted(plan.nm_ids)
    first = nm_sorted[0]
    ps = period_start.strftime("%Y%m%d")
    pe = period_end.strftime("%Y%m%d")
    return f"sub{subject_id}-g{first}-p{ps}-{pe}"

async def purchase_group(
    *,
    client: WBCabinetClient,
    plan: GroupPlan,
    period_start: date,
    period_end: date,
) -> dict:
    """POST /api/v2/competitor-comparison/nms — атомарно списывает 1 слот для новой группы."""
    body = {
        "nmIDs": sorted(plan.nm_ids),
        "periodStart": period_start.isoformat(),
        "periodEnd": period_end.isoformat(),
    }
    return await client.post(
        "/ns/analytics-api/content-analytics/api/v2/competitor-comparison/nms",
        json=body,
    )
```

### `etl/funnel.py` (response → DB)

```python
from datetime import datetime, UTC
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from ..models import CmpGroup, CmpFunnelDaily

async def seed_cmp_group_from_payload(session, *, comparison_id, payload, ingest_run_id, **extras):
    """Создать row в cmp_groups (если не существует)."""
    now = int(datetime.now(UTC).timestamp())
    nm_ids = sorted(int(p["product"]["nmId"]) for p in payload["data"]["products"])
    period_start = payload["data"]["period"]["start"]  # ISO date string
    period_end = payload["data"]["period"]["end"]
    period_start_epoch = _iso_to_epoch_midnight_utc(period_start)
    period_end_epoch = _iso_to_epoch_midnight_utc(period_end)
    subject_id = _extract_subject_id_from_cmp_id(comparison_id)
    stmt = sqlite_insert(CmpGroup).values(
        comparison_id=comparison_id,
        subject_id=subject_id,
        nm_ids_json=json.dumps(nm_ids),
        period_start=period_start_epoch,
        period_end=period_end_epoch,
        created_at=now,
        expires_at=now + 30 * 86400,
        status="ready",
        ingest_run_id=ingest_run_id,
    )
    stmt = stmt.on_conflict_do_nothing(index_elements=["comparison_id"])
    await session.execute(stmt)

async def upsert_funnel_daily_from_comparison(session, *, comparison_id, comparison_response, ingest_run_id):
    """Parse dayDynamics → upsert cmp_funnel_daily."""
    rows = []
    for entry in comparison_response.get("data", {}).get("dayDynamics", []):
        rows.append({
            "comparison_id": comparison_id,
            "nm_id": int(entry["nmID"]),
            "date": _iso_to_epoch_midnight_msk(entry["dt"]),
            "open_card_count": int(entry["openCardCount"]),
            "add_to_cart_count": int(entry["addToCartCount"]),
            "orders_count": int(entry["orderCount"]),
            "orders_sum_kopeks": int(entry["orderSum"] * 100),
            "ingest_run_id": ingest_run_id,
        })
    if not rows:
        return {"rows_written": 0}
    stmt = sqlite_insert(CmpFunnelDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["comparison_id", "nm_id", "date"],
        set_={k: stmt.excluded[k] for k in (
            "open_card_count", "add_to_cart_count", "orders_count", "orders_sum_kopeks"
        )},
    )
    await session.execute(stmt)
    return {"rows_written": len(rows)}
```

### `etl/excel.py` (XLSX import)

```python
from pathlib import Path
import io, httpx, zipfile, time, asyncio
from python_calamine import CalamineWorkbook

async def import_comparison_to_db(client, *, nm_ids, period_start, period_end, engine, ingest_run_id, comparison_id):
    """1. POST /file-manager/download → poll → download ZIP. 2. Parse XLSX. 3. Upsert DB."""
    # Step 1: request export
    response = await client.post(
        "/ns/analytics-api/content-analytics/api/v1/file-manager/download",
        json={"cmpID": comparison_id, "type": "comparison-full"},
    )
    download_id = response["downloadId"]
    # Step 2: poll until ready
    for _ in range(60):
        await asyncio.sleep(2)
        r = await client.get("/ns/analytics-api/content-analytics/api/v1/file-manager/downloads")
        for d in r.get("downloads", []):
            if d["downloadId"] == download_id and d["status"] == "ready":
                url = d["url"]
                break
        else:
            continue
        break
    else:
        raise TimeoutError("Excel export not ready after 120 sec")
    # Step 3: download ZIP
    async with httpx.AsyncClient() as fetcher:
        r = await fetcher.get(url)
        zip_data = r.content
    # Step 4: parse + upsert
    zip_path = Path(f"data/cabinet/excel/{download_id}.zip")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    zip_path.write_bytes(zip_data)
    stats = {}
    with zipfile.ZipFile(zip_path) as zf:
        for name in zf.namelist():
            if name.endswith(".xlsx"):
                with zf.open(name) as xlsx_f:
                    wb = CalamineWorkbook.from_filelike(io.BytesIO(xlsx_f.read()))
                    # Parse sheets, upsert into appropriate tables
                    # (Implementation details omitted — see docs/06-excel-ingest.md)
                    pass
    return zip_path, None, stats
```

## Step-by-step adaptation для друга

1. **Создай structure:**
   ```bash
   mkdir -p src/wb_pool/{discovery,models,clients,purchase,etl}
   touch src/wb_pool/{__init__.py,config.py,logging_setup.py,db.py,cli.py}
   touch src/wb_pool/{discovery,models,clients,purchase,etl}/__init__.py
   ```

2. **Скопируй provided code:**
   ```bash
   SKILL=~/Desktop/wb-cabinet-purchases-skill
   cp $SKILL/code/algo_i.py src/wb_pool/discovery/
   cp $SKILL/code/algo_i_types.py src/wb_pool/discovery/
   cp $SKILL/code/title_kw_extractor.py src/wb_pool/discovery/
   cp $SKILL/code/serp_with_feedbacks.py src/wb_pool/discovery/
   cp $SKILL/code/mpstats_topup.py src/wb_pool/discovery/mpstats_layer.py
   cp $SKILL/code/cli_*.py src/wb_pool/
   ```

3. **Реализуй STUB модули** (см. псевдокод выше). Минимум для запуска discovery:
   - `config.py`
   - `logging_setup.py`
   - `db.py`
   - `clients/serp.py`
   - `clients/wb_seller.py`
   - `clients/sales_funnel.py`
   - `clients/card_detail.py`
   - `models/` ORM (см. `schemas/db.sql`)

4. **Для покупки доп.:**
   - `clients/cabinet.py`
   - `clients/cookies.py`
   - `purchase/groups.py`
   - `etl/funnel.py`

5. **Для Excel ingest доп.:**
   - `etl/excel.py`

6. **Проверь:**
   ```bash
   uv run mypy --strict src
   uv run python -c "from wb_pool.discovery.algo_i import run_algo_i; print('OK')"
   ```

## Альтернатива: использовать наш wbap проект как стартовый

Если друг согласен на наш namespace `wbap` — можно взять весь wbap codebase, удалить Semily-specific brand markers (`{"semily", "семили"}` → его brand markers), и использовать as-is. Это **быстрее** чем переписывать stubs.

Минусы: лишний код (foundation orchestrator который, возможно, ему не нужен).

Плюсы: всё работает out of the box.

## Что должен помнить агент при адаптации

1. **Импорты** в provided code/ файлах ссылаются на `wb_pool.*` namespace — это **целевая** структура. Если у друга namespace другой (e.g. `mybrand_competitor.*`) — переименуй через `sed`.
2. **`MPStatsListingRow`** в `mpstats_layer.py` нужен **только** для функции `load_baseline_topup` (DB cache). Если cache не нужен — удали эту функцию.
3. **`brand_markers`** должны быть **в lowercase**. Проверь `.brand_markers_set` в `config.py`.
4. **Конфиг models через alembic migrations**, не raw SQL. См. `schemas/db.sql` для справки.
