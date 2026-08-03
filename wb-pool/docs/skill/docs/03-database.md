# База данных — схема и принципы

## Принципы

1. **Append-only.** Никаких UPDATE без явной seller-инициации. UPSERT с UNIQUE constraint на natural key + snapshot_date.
2. **Все timestamps — INTEGER epoch UTC.** SQLite range scans на INTEGER в 10-100× быстрее ISO TEXT.
3. **Все цены — INTEGER в копейках.** Никаких FLOAT для денег (precision loss).
4. **Snapshot date** в каждой row — для time-series history (3+ years horizon).
5. **`raw_runs` FK везде** — каждая row знает откуда взялась (audit trail).
6. **SQLite WAL mode** по умолчанию (concurrent reads, single writer). PostgreSQL для production scale.

## Схема — 8 ключевых таблиц

Полный SQL в [schemas/db.sql](../schemas/db.sql). Сводно:

### 1. `raw_runs` — журнал ingest runs

```sql
CREATE TABLE raw_runs (
  id          INTEGER PRIMARY KEY,
  run_id      TEXT NOT NULL UNIQUE,     -- e.g. "discover-subj357-1779316531"
  started_at  INTEGER NOT NULL,         -- epoch UTC
  command     TEXT NOT NULL,            -- e.g. "discover", "buy-pool", "ingest-excel"
  status      TEXT NOT NULL,            -- 'running', 'done', 'failed'
  finished_at INTEGER,
  error_msg   TEXT
);
```

Все остальные таблицы имеют `ingest_run_id FK → raw_runs.id`. Это позволяет:
- Сделать rollback (DELETE WHERE ingest_run_id = X)
- Audit trail кто/когда/каким run закачал данные
- Параллельные runs с разными ID не путаются

### 2. `wb_cards` — мои карточки (Seller API content)

```sql
CREATE TABLE wb_cards (
  nm_id           INTEGER PRIMARY KEY,
  vendor_code     TEXT NOT NULL,
  brand           TEXT,                -- моё brand name (e.g. 'MyBrand')
  title           TEXT,
  subject_id      INTEGER,             -- WB категория ID
  subject_name    TEXT,
  is_archived     INTEGER NOT NULL DEFAULT 0,
  photos_json     TEXT,
  sizes_json      TEXT,
  updated_at_wb   INTEGER NOT NULL,    -- последнее обновление в кабинете WB
  fetched_at      INTEGER NOT NULL,    -- когда мы скачали
  ingest_run_id   INTEGER NOT NULL REFERENCES raw_runs(id)
);
CREATE INDEX ix_wb_cards_subject ON wb_cards(subject_id, is_archived);
```

Используется:
- Worker 2 (narrow keywords) — берёт title для извлечения keyword candidates
- Validation — найти свои топ-карточки

### 3. `cmp_groups` — купленные группы сравнений (5 nm/группа)

```sql
CREATE TABLE cmp_groups (
  id               INTEGER PRIMARY KEY,
  comparison_id    TEXT NOT NULL UNIQUE,    -- e.g. "sub357-g123456-p20260219-20260520"
  subject_id       INTEGER NOT NULL,
  nm_ids_json      TEXT NOT NULL,           -- JSON array [123456, 789012, ...] 5 элементов
  period_start     INTEGER NOT NULL,        -- epoch UTC
  period_end       INTEGER NOT NULL,
  created_at       INTEGER NOT NULL,
  expires_at       INTEGER NOT NULL,        -- ~30 дней после created
  status           TEXT NOT NULL CHECK(status IN ('purchasing','ready','expired','failed')),
  ingest_run_id    INTEGER NOT NULL REFERENCES raw_runs(id)
);
```

`comparison_id` — детерминированный hash от (subject_id, sorted_nm_ids, period_start, period_end). Это позволяет:
- Idempotent повторная покупка той же группы → 0 слотов
- Resume после crash (можно проверить status='purchasing' и retry)

### 4. `cmp_funnel_daily` — funnel data per nm × date

```sql
CREATE TABLE cmp_funnel_daily (
  id                  INTEGER PRIMARY KEY,
  comparison_id       TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  nm_id               INTEGER NOT NULL,
  date                INTEGER NOT NULL,        -- epoch UTC midnight MSK
  open_card_count     INTEGER NOT NULL,         -- открытий карточки за день
  add_to_cart_count   INTEGER NOT NULL,         -- в корзину
  orders_count        INTEGER NOT NULL,         -- заказов
  orders_sum_kopeks   INTEGER NOT NULL,         -- сумма заказов в копейках
  ingest_run_id       INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, nm_id, date)
);
CREATE INDEX ix_cmp_funnel_nm_date ON cmp_funnel_daily(nm_id, date);
```

Это **главная таблица revenue/conversion data**:
- Total revenue per nm = `SUM(orders_sum_kopeks) WHERE nm_id = X AND date BETWEEN ...`
- Conversion ratio = `orders_count / open_card_count` (вычисляем через VIEW)
- Time-series — append-only, никогда не перезаписываем historical rows

### 5. `cmp_search_queries` — keywords для группы

```sql
CREATE TABLE cmp_search_queries (
  id                  INTEGER PRIMARY KEY,
  comparison_id       TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  keyword             TEXT NOT NULL,
  period_start        INTEGER NOT NULL,
  period_end          INTEGER NOT NULL,
  frequency           INTEGER NOT NULL,         -- частотность запроса
  frequency_dynamics  INTEGER,                  -- динамика (delta vs prev period)
  snapshot_date       INTEGER NOT NULL DEFAULT 0,
  ingest_run_id       INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, keyword, period_start, period_end, snapshot_date)
);
```

И `cmp_search_query_per_nm` (per-keyword × nm metrics):

```sql
CREATE TABLE cmp_search_query_per_nm (
  id                      INTEGER PRIMARY KEY,
  comparison_id           TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  keyword                 TEXT NOT NULL,
  nm_id                   INTEGER NOT NULL,
  period_start            INTEGER NOT NULL,
  period_end              INTEGER NOT NULL,
  cart_from_search_raw    INTEGER,
  order_from_search_raw   INTEGER,
  cart_conv_pct_raw       FLOAT,
  order_conv_pct_raw      FLOAT,
  is_rounded_pct          INTEGER NOT NULL DEFAULT 0,
  snapshot_date           INTEGER NOT NULL DEFAULT 0,
  ingest_run_id           INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, keyword, nm_id, period_start, period_end, snapshot_date)
);
```

⚠ **Внимание:** `_raw` суффиксы означают данные **как они в Excel** — некоторые из них с округлением 1% precision. Если `is_rounded_pct=1` — конверсии округлены WB до целых процентов, не используй их для точных расчётов.

### 6. `cmp_warehouse_metrics` — складская динамика

```sql
CREATE TABLE cmp_warehouse_metrics (
  id              INTEGER PRIMARY KEY,
  comparison_id   TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  nm_id           INTEGER NOT NULL,
  warehouse_name  TEXT NOT NULL,            -- e.g. "Коледино", "Электросталь"
  metric_type     TEXT NOT NULL,            -- 'stock', 'sales', 'delivery'
  metric_value    INTEGER NOT NULL,
  period_start    INTEGER NOT NULL,
  period_end      INTEGER NOT NULL,
  ingest_run_id   INTEGER NOT NULL REFERENCES raw_runs(id)
);
```

### 7. `cmp_size_stocks` — остатки по размерам

```sql
CREATE TABLE cmp_size_stocks (
  id              INTEGER PRIMARY KEY,
  comparison_id   TEXT NOT NULL,
  nm_id           INTEGER NOT NULL,
  size_name       TEXT NOT NULL,             -- e.g. "S", "42", "default"
  stock_count     INTEGER NOT NULL,
  date            INTEGER NOT NULL,
  ingest_run_id   INTEGER NOT NULL
);
```

Для категорий без размеров (Кремы, Шампуни) — таблица пустая, это нормально.

### 8. `mpstats_keywords` — keywords per моя карточка из MPStats

```sql
CREATE TABLE mpstats_keywords (
  id              INTEGER PRIMARY KEY,
  nm_id           INTEGER NOT NULL,           -- моя карточка
  keyword         TEXT NOT NULL,
  date            INTEGER NOT NULL,           -- snapshot date epoch UTC midnight MSK
  avg_position    INTEGER NOT NULL,           -- средняя позиция в SERP за период
  traffic         INTEGER NOT NULL,           -- условный traffic score MPStats
  visibility_pct  FLOAT,                      -- % видимости
  ingest_run_id   INTEGER NOT NULL,
  UNIQUE(nm_id, keyword, date)
);
CREATE INDEX ix_mpstats_keywords_nm_pos_traffic ON mpstats_keywords(nm_id, avg_position, traffic);
```

Используется Worker 2 (narrow keywords):
- `SELECT keyword FROM mpstats_keywords WHERE nm_id = X AND avg_position <= 20 AND traffic > 0 ORDER BY traffic DESC`

### 9. (Optional) `analytic_category_pool` — исторический пул

Если хочешь хранить production pool между запусками:

```sql
CREATE TABLE analytic_category_pool (
  id                 INTEGER PRIMARY KEY,
  my_nm_id           INTEGER,                  -- если pool привязан к конкретной карточке (per-card)
  subject_id         INTEGER NOT NULL,         -- per-category pool
  competitor_nm_id   INTEGER NOT NULL,
  snapshot_date      INTEGER NOT NULL,
  source             TEXT NOT NULL,            -- 'serp_main', 'mpstats', 'narrow_serp', ...
  rank               INTEGER,
  is_top30           INTEGER NOT NULL DEFAULT 0,
  ingest_run_id      INTEGER NOT NULL,
  UNIQUE(subject_id, competitor_nm_id, snapshot_date)
);
```

Опциональная — не обязательна для базового workflow. Полезна если хочешь diff текущий pool vs предыдущий запуск.

## Views (вычисляемые поля)

```sql
-- Conversion ratios без хранения (decimal precision)
CREATE VIEW v_cmp_funnel_with_conversions AS
SELECT
  *,
  CAST(add_to_cart_count AS REAL) / NULLIF(open_card_count, 0) AS cart_conv_pct,
  CAST(orders_count AS REAL) / NULLIF(open_card_count, 0) AS order_conv_pct,
  CAST(orders_count AS REAL) / NULLIF(add_to_cart_count, 0) AS cart_to_order_pct
FROM cmp_funnel_daily;
```

## Migrations через alembic

```bash
# Создать новую миграцию (auto-generate from models)
uv run alembic revision --autogenerate -m "add wb_orders table"

# Применить все миграции
uv run alembic upgrade head

# Откатить одну
uv run alembic downgrade -1
```

Файлы в `alembic/versions/`. **Никогда не редактируй committed миграции** — создавай новую.

## Принципы запросов

### Time-range queries

```python
# Правильно: int epoch с indexed range scan
WHERE date >= :ps_epoch AND date <= :pe_epoch

# Неправильно: TEXT компарисон (медленнее в 100×)
WHERE date >= '2026-04-21'
```

### Idempotent upsert

```python
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
stmt = sqlite_insert(cmp_funnel_daily).values(rows)
stmt = stmt.on_conflict_do_update(
    index_elements=['comparison_id', 'nm_id', 'date'],
    set_={'orders_count': stmt.excluded.orders_count, ...}
)
await session.execute(stmt)
```

### Не загружай весь dataset в Python

```python
# Плохо
all_rows = await session.execute(select(CmpFunnelDaily))  # может быть 100k+ rows
for row in all_rows: ...

# Хорошо — stream
result = await session.stream(select(CmpFunnelDaily))
async for row in result:
    ...
```

## Что должен помнить агент про БД

1. **`subject_id`** — это **integer ID категории на WB** (e.g. 357 = Кремы). Их можно посмотреть в WB cabinet UI или через `GET /api/v2/parent-subjects`.
2. **`comparison_id`** — это не nm_id, это ID **группы из 5 nm**. Каждая cmp_id уникальна на (subject, [nm1..nm5], period).
3. **`raw_runs.id`** в FK везде — для bookkeeping. Не вычитай row без `ingest_run_id`.
4. **`is_archived=1`** в `wb_cards` означает что карточка снята с продажи на WB. Не использовать в Discovery как seed.
5. **MPStats numeric fields** (`mpstats_listing_row` если используется) часто NULL — это нормально, не считать как corruption.

## Health check

```bash
uv run wb-pool db-doctor

# Ожидается:
# ✅ Schema: alembic head 0001
# ✅ Tables: 8/8 present
# ✅ Indexes: all present
# ✅ Foreign keys: enabled (SQLite)
# ✅ WAL mode: enabled
# 📊 Stats:
#    raw_runs:                 0 rows
#    wb_cards:                 0 rows
#    cmp_groups:               0 rows
#    cmp_funnel_daily:         0 rows
#    ...
```

После онбординга + первого `wb-pool pull-cards` (загрузка своих) — должно появиться `wb_cards: N rows`.

## Что дальше

→ [04-discovery-workers.md](04-discovery-workers.md) — как работают 4 worker'а
