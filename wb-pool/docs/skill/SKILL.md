---
name: wb-cabinet-purchases
description: Use when WB seller wants to build competitor pool + buy cabinet comparisons. Covers onboarding (получение Seller API + MPStats tokens + cabinet cookies через browser_cookie3), discovery через 4 независимых слоя (SERP main, SERP narrow по моим keywords, MPStats 3 windows, optional DB earners), purchasing в группах 5 nm с idempotent dedup, Excel ingest (funnel + keywords + warehouse), validation отчёт. Универсально для любого WB subject_id. Триггеры — "купить сравнения WB", "найти конкурентов на Wildberries", "competitor pool", "cabinet comparisons", "WB кабинет сравнения".
---

# WB Cabinet Purchases — End-to-End Pipeline

Полный production-ready гайд для AI агента по работе с покупками сравнений Wildberries. Покрывает онбординг (cookies + токены), discovery конкурентов (4 независимых слоя), покупку cabinet сравнений (по 5 nm/группа), Excel ingest, валидацию.

## 🚀 Где начать (для AI агента)

1. **[BOOTSTRAP.md](BOOTSTRAP.md)** — пошаговый setup за ~1 час (commands ready to run)
2. **[code/STRUCTURE.md](code/STRUCTURE.md)** — что в provided code, что нужно реализовать самому
3. **[docs/01-onboarding.md](docs/01-onboarding.md)** — детальный onboarding (токены, cookies, validation)
4. Дальше docs/02 → docs/09 по порядку

**⚠ Provided code НЕ является plug-and-play.** Это reference implementation главного алгоритма + clear contracts для остальных модулей. См. [STRUCTURE.md](code/STRUCTURE.md) для понимания границ.

## ⚠️ Критические правила безопасности

**ПРОЧИТАЙ ЭТО ПЕРВЫМ. Несоблюдение ведёт к потере денег и cabinet квоты.**

1. **Каждая покупка сравнения списывает 1 cabinet слот.** Месячный бюджет ~1100 слотов. Слот теряется атомарно и НЕОБРАТИМО при первой purchase каждой уникальной группы 5 nm.
2. **Перед любым live-запуском который тратит слоты — `--pre-flight`/`--dry-run` сначала.** Показать оператору точные числа (сколько слотов, сколько MPStats ops, сколько SERP), ждать явное `y` в чате. Это правило экономит десятки слотов от случайной траты.
3. **MPStats numeric fields ненадёжные.** Поля `revenue_kopeks/sales/balance/final_price_*` в нашей БД могут быть NULL by design — это **не баг**. Реальные revenue получаются из cmp_funnel_daily (cabinet) или wb_orders/wb_sales (Seller API). Не строить аналитику на MPStats numbers.
4. **MPStats `d2` (период end) должен быть ≤ today-1.** `d2 = today` возвращает HTTP 422. Используй `yesterday` как safe верхний bound.
5. **Cabinet cookies экспирируют** через ~1-2 недели. Re-import через `browser_cookie3` из реального Chrome (см. `docs/01-onboarding.md`).
6. **WB SERP throttle.** При интенсивном использовании (10+ запусков в час) WB возвращает 429. Backoff retry в коде; в production это редко проблема (1 запуск в день/неделю).
7. **Conventional commits** в коде, без emoji, без AI co-author footer.

## Workflow по шагам

```
[ОНБОРДИНГ] → [НАСТРОЙКА ПРОЕКТА] → [БД] → [ДИСКАВЕРИ] → [ПОКУПКА] → [ИНГЕСТ] → [ВАЛИДАЦИЯ]
     1            2                 3        4             5           6           7
```

Каждый шаг описан в отдельном sub-doc. Читай в порядке.

### 1. [Онбординг](docs/01-onboarding.md)

Получение:
- **WB Seller API token** (Personal токен на 180 дней) — из кабинета продавца, scopes: Content/Statistics/Analytics/Prices/Marketplace
- **MPStats API token** — из mpstats.io/api настроек
- **WB Cabinet cookies** (для покупки сравнений) — через `browser_cookie3` из обычной Chrome-сессии после логина в seller-content.wildberries.ru

Агент должен:
- Открыть Chrome для пользователя
- Дать ссылки куда идти
- Запросить токены безопасно (через .env)
- Проверить валидность каждого канала через canary запросы

### 2. [Настройка проекта](docs/02-project-setup.md)

Python 3.11+, uv, dependencies:
- httpx, curl_cffi (TLS-fingerprint для WB SERP), browser_cookie3 (cookies)
- SQLAlchemy 2.0 async, alembic, aiosqlite
- typer (CLI), rich (output), structlog (logging)
- python-calamine + openpyxl (Excel parsing)
- pytest + respx (тесты)

Структура проекта по src-layout.

### 3. [База данных](docs/03-database.md)

SQLite в WAL-режиме (опционально PostgreSQL). 8 ключевых таблиц:
- `raw_runs` — журнал каждого ingest run для audit trail
- `wb_cards` — мои карточки (Seller API content)
- `cmp_groups` — купленные группы сравнений (5 nm/группа)
- `cmp_funnel_daily` — funnel data per nm × date (revenue/orders/views)
- `cmp_search_queries` + `cmp_search_query_per_nm` — keywords с conversions
- `cmp_warehouse_metrics` — складская динамика
- `mpstats_keywords` — keywords per моя карточка из MPStats
- `analytic_category_pool` — исторический пул конкурентов (опционально)

Агент должен:
- Создать БД через alembic
- Применить миграции
- Проверить schema через `doctor` команду

### 4. [Дискавери — 4 worker'а](docs/04-discovery-workers.md)

Каждый worker независим. Все применяют `feedbacks_threshold` фильтр independently.

**Worker 1: SERP main (top-N category-level)**
- 1 запрос по subject_name через curl_cffi (chrome116 impersonation)
- Default: top-100, configurable `--serp-top`
- Filter: feedbacks ≥ 300 (configurable)
- Output: ~80-100 unique nm

**Worker 2: SERP narrow (по моим узким ключам)**
- Перед запуском извлекает ~10 unique narrow keywords из моих топ-N карточек через `mpstats_keywords` (title token match)
- N запросов по этим keywords через SERP
- Default: top-20 на ключ (= serp_top // 5), configurable `--serp-narrow-top`
- Filter: feedbacks ≥ 300
- Output: ~50-300 unique nm в зависимости от широты категории

**Worker 3: MPStats 3 окна (top by revenue)**
- `POST /category/items` за 3 окна: live (последние 30 дней), june_2025, year_2025
- Default: top-25 на окно (=serp_top // 4 = 120 total max), configurable `--mpstats-per-window`
- Filter: comments ≥ 300 (comments = WB feedbacks counterpart)
- Output: ~50-90 unique nm
- ⚠ Стоит 3 MPStats ops из 150/мес квоты

**Worker 4 (optional): DB earners ≥1M ₽/30d**
- Если есть production pool в `analytic_category_pool` для этого subject_id — берёт всех cabinet-known earners ≥1M ₽/30d
- Бесплатно (только SQL), полезно если pool уже накопился исторически
- Output: ~50-150 nm в зависимости от истории

### 5. [Дедупликация + покупка](docs/05-purchase-pipeline.md)

**Union + дедуп:**
- Union всех 4 workers → unique nm set
- Drop carto удалённые на WB (через CardDetail batch endpoint)
- Group by 5 nm (cabinet quantum) с padding из existing nm если последняя группа короче

**Purchase saga:**
- Per группа: `POST /api/v2/competitor-comparison/nms` → ответ с funnel JSON
- Парсинг → upsert в `cmp_funnel_daily`
- Idempotent: повторная покупка той же группы 5 nm = 0 слотов (через `/limits.availableWithoutCharge`)
- ⚠ Только новые комбинации = 1 слот каждая

**Atomic safety:**
- Per группа try/except → log + continue (не валит всю batch)
- DLQ для failed (retry потом)

### 6. [Excel ingest](docs/06-excel-ingest.md)

После покупки cmp_id Excel доступен **бесплатно** (через `/file-manager/download`):
- ZIP с XLSX внутри
- Парсинг calamine (10× быстрее openpyxl) с openpyxl fallback
- Upsert в 4 таблицы:
  - `cmp_search_queries` (keywords для группы)
  - `cmp_search_query_per_nm` (keyword × nm → orders/conversions)
  - `cmp_warehouse_metrics` (склад × nm → объёмы)
  - `cmp_size_stocks` (размер × nm → остатки)

### 7. [Validation + отчёт](docs/07-validation.md)

После полного цикла:
- Tier distribution по revenue 30d (sticky-tier ≥1M / mid 500k-1M / low 100k-500k / trash <100k)
- Per-layer quality (какой слой дал больше мусора)
- Top-N новых найденных карточек
- My carto coverage (мои в pool как sanity check)

### 8. [Оркестратор](docs/08-orchestrator.md)

Связывает всё в одну команду:
```bash
wb-pool run-category --subject-id 357 --subject-name "Кремы"
```

Логика:
1. Preflight (cookies валидные? cabinet квота? MPStats budget?)
2. Discovery (4 workers параллельно где возможно)
3. Dedup + group by 5
4. **STOP** → показать pre-flight, ждать `y`
5. Purchase saga (per группа try/except, DLQ)
6. Excel ingest
7. Validation report
8. Logging всего через structlog

## Где смотреть код

Готовые модули в [code/](code/):

| Файл | Что делает |
|---|---|
| `algo_i.py` | Оркестратор discovery + 4 worker'а |
| `algo_i_types.py` | Config + Result dataclasses |
| `title_kw_extractor.py` | Извлечение narrow keywords из моих карточек |
| `serp_with_feedbacks.py` | SERP запросы с feedbacks через curl_cffi |
| `mpstats_topup.py` | MPStats 3 окна fetcher |
| `buy_pool.py` | Покупка cabinet сравнений по pool |
| `ingest_comparison_excel.py` | Excel парсинг + ingest |
| `cabinet_client.py` | WB Cabinet HTTP клиент (httpx + cookies) |
| `cookies_loader.py` | browser_cookie3 wrapper |
| `serp_client.py` | curl_cffi SERP клиент с version cascade |
| `card_detail.py` | WB public CardDetail batch fetcher |
| `sales_funnel.py` | WB Seller API sales-funnel клиент (Jam-tier) |

## CLI шпаргалка

```bash
# Setup
wb-pool setup-cookies               # browser_cookie3 import
wb-pool doctor                      # canary check всех источников

# Discovery only (0 cabinet slots)
wb-pool discover --subject-id 357 --subject-name "Кремы" --pre-flight
wb-pool discover --subject-id 357 --subject-name "Кремы"
# → data/pools/subject-357-<ts>.json

# Purchase by pool JSON
wb-pool buy --json data/pools/subject-357-<ts>.json --subject-id 357 \
            --subject-name "Кремы"                                    # dry-run
wb-pool buy --json data/pools/subject-357-<ts>.json --subject-id 357 \
            --subject-name "Кремы" --execute                          # live

# Excel ingest (free for purchased cmp_id)
wb-pool ingest-excel --subject-id 357 --period-start 2026-02-21 --period-end 2026-05-21

# Validation
wb-pool report --subject-id 357

# All-in-one (orchestrator)
wb-pool run-category --subject-id 357 --subject-name "Кремы"
```

## Что отрегулировать перед использованием

В `templates/.env.example` есть placeholders — заполни:
- `WB_API_TOKEN=` — Seller API Personal токен (180 дней)
- `MPSTATS_API_TOKEN=` — MPStats Analytics v1
- `MY_BRAND_MARKERS=brand_name_lower,второе_написание` — для фильтра keywords (защита от вытаскивания брендовых ключей в narrow_serp)
- `DATABASE_URL=sqlite+aiosqlite:///./data/wb-pool.db` (по умолчанию SQLite)
- `MPSTATS_PARENT_CATEGORY=Красота` — top-level категория для MPStats path (зависит от subject)

В `templates/pyproject.toml` есть базовый стек — адаптируй под свои нужды.

## Что НЕ покрывается этим скиллом

- Создание собственных карточек на WB
- Управление ценами / акциями
- Работа с advertising кампаниями WB
- Парсинг конкурентских отзывов или фото
- Аналитика собственных продаж (это отдельный workflow через wb_orders/wb_sales)

## Если что-то сломалось

1. `wb-pool doctor` — canary check (валидность токенов, cookies, DB)
2. Проверь логи в `logs/` (structlog JSON)
3. `docs/06-troubleshooting.md` — частые проблемы
4. Перезапусти cookies setup если 401/403 на cabinet API

## Архитектурные принципы (для AI агента)

При работе с этим проектом:

1. **TDD строго** для testable parts (pure functions, ETL parsers). Каждая новая функция = RED test → GREEN impl → commit.
2. **R&D без тестов** для orchestrators (smoke via live runs). Это OK.
3. **mypy --strict + ruff clean** per commit обязательно.
4. **Append-only БД.** Никаких DELETE, только UPSERT с UNIQUE constraint на natural key + snapshot_date.
5. **Все timestamps** — INTEGER epoch UTC (10-100× быстрее ISO TEXT range scans в SQLite).
6. **Все цены** — INTEGER в копейках (избежать float precision).
7. **Сырые числа всегда**, агрегаты вычисляем views'ами (не теряем данные).
8. **Idempotency** — все ETL handlers должны переживать повторный запуск без duplication.
9. **Per-source bulkhead** (httpx + aiolimiter + semaphore) — один сломанный источник не валит весь pipeline.
10. **Conventional commits, no emoji, no AI footer.**

## License & Disclaimer

См. [DISCLAIMER.md](DISCLAIMER.md). Этот pipeline бьёт public/cabinet/MPStats APIs WB и mpstats.io — соблюдай их Terms of Service. Не пытайся обойти rate limits через VPN/прокси (WB банит IP).
