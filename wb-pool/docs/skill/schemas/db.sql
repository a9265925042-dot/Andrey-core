-- WB Cabinet Pool Builder — full DB schema
-- SQLite (PostgreSQL compatible — see comments below).
-- Apply through alembic migrations, не raw SQL для production.

-- ────────────────────────────────────────────────────────────────────────────
-- 0. PRAGMA (SQLite-specific) — set in alembic env.py
-- ────────────────────────────────────────────────────────────────────────────
-- PRAGMA journal_mode = WAL;
-- PRAGMA foreign_keys = ON;
-- PRAGMA synchronous = NORMAL;

-- ────────────────────────────────────────────────────────────────────────────
-- 1. raw_runs — журнал ingest runs (audit trail)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE raw_runs (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT NOT NULL UNIQUE,
  started_at   INTEGER NOT NULL,            -- epoch UTC
  command      TEXT NOT NULL,                -- 'discover' / 'buy' / 'ingest-excel' / ...
  status       TEXT NOT NULL DEFAULT 'running' CHECK(status IN ('running','done','failed')),
  finished_at  INTEGER,
  error_msg    TEXT
);
CREATE INDEX ix_raw_runs_command_started ON raw_runs(command, started_at DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 2. wb_cards — мои карточки (Seller API content)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE wb_cards (
  nm_id              INTEGER PRIMARY KEY,
  nm_uuid            TEXT,
  imt_id             INTEGER,
  vendor_code        TEXT NOT NULL,
  brand              TEXT,
  title              TEXT,
  subject_id         INTEGER,
  subject_name       TEXT,
  is_archived        INTEGER NOT NULL DEFAULT 0,
  photos_json        TEXT,
  sizes_json         TEXT,
  dimensions_length  REAL,
  dimensions_width   REAL,
  dimensions_height  REAL,
  dimensions_weight  REAL,
  created_at_wb      INTEGER,
  updated_at_wb      INTEGER NOT NULL,
  fetched_at         INTEGER NOT NULL,
  ingest_run_id      INTEGER NOT NULL REFERENCES raw_runs(id)
);
CREATE INDEX ix_wb_cards_subject ON wb_cards(subject_id, is_archived);
CREATE INDEX ix_wb_cards_brand ON wb_cards(brand);

-- ────────────────────────────────────────────────────────────────────────────
-- 3. mpstats_keywords — keywords per моя карточка (для narrow keywords extractor)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE mpstats_keywords (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  nm_id          INTEGER NOT NULL,
  keyword        TEXT NOT NULL,
  date           INTEGER NOT NULL,                 -- epoch UTC midnight MSK
  avg_position   INTEGER NOT NULL,                 -- avg SERP rank за период
  traffic        INTEGER NOT NULL,                 -- traffic score MPStats
  visibility_pct REAL,
  ingest_run_id  INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(nm_id, keyword, date)
);
CREATE INDEX ix_mpstats_keywords_nm_pos_traffic ON mpstats_keywords(nm_id, avg_position, traffic DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- 4. cmp_groups — купленные группы (5 nm/группа)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_groups (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id    TEXT NOT NULL UNIQUE,
  subject_id       INTEGER NOT NULL,
  name             TEXT,
  nm_ids_json      TEXT NOT NULL,                  -- JSON array [12345, 67890, ...]
  period_start     INTEGER NOT NULL,               -- epoch UTC
  period_end       INTEGER NOT NULL,
  created_at       INTEGER NOT NULL,
  expires_at       INTEGER NOT NULL,
  status           TEXT NOT NULL DEFAULT 'purchasing'
                   CHECK(status IN ('purchasing','ready','expired','failed')),
  main_nm_id       INTEGER,                        -- первая nm в группе для display
  wb_export_uuid   TEXT,                            -- WB internal export ID для Excel download
  ingested_at      INTEGER NOT NULL DEFAULT 0,
  ingest_run_id    INTEGER NOT NULL REFERENCES raw_runs(id)
);
CREATE INDEX ix_cmp_groups_subject_created ON cmp_groups(subject_id, created_at DESC);
CREATE INDEX ix_cmp_groups_status ON cmp_groups(status);

-- ────────────────────────────────────────────────────────────────────────────
-- 5. cmp_funnel_daily — daily funnel data per nm × date
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_funnel_daily (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id       TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  nm_id               INTEGER NOT NULL,
  date                INTEGER NOT NULL,                -- epoch UTC midnight MSK
  open_card_count     INTEGER NOT NULL,
  add_to_cart_count   INTEGER NOT NULL,
  orders_count        INTEGER NOT NULL,
  orders_sum_kopeks   INTEGER NOT NULL,                -- РУБЛИ × 100 → копейки (precision)
  ingest_run_id       INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, nm_id, date)
);
CREATE INDEX ix_cmp_funnel_nm_date ON cmp_funnel_daily(nm_id, date);

-- ────────────────────────────────────────────────────────────────────────────
-- 6. cmp_search_queries — keywords per группа (aggregate level)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_search_queries (
  id                  INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id       TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  keyword             TEXT NOT NULL,
  period_start        INTEGER NOT NULL,
  period_end          INTEGER NOT NULL,
  frequency           INTEGER NOT NULL,                -- частотность запроса
  frequency_dynamics  INTEGER,                          -- delta vs prev period
  snapshot_date       INTEGER NOT NULL DEFAULT 0,
  ingested_at         INTEGER NOT NULL DEFAULT 0,
  ingest_run_id       INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, keyword, period_start, period_end, snapshot_date)
);
CREATE INDEX ix_cmp_search_queries_kw ON cmp_search_queries(keyword);

-- ────────────────────────────────────────────────────────────────────────────
-- 7. cmp_search_query_per_nm — keyword × nm conversions
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_search_query_per_nm (
  id                      INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id           TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  keyword                 TEXT NOT NULL,
  nm_id                   INTEGER NOT NULL,
  period_start            INTEGER NOT NULL,
  period_end              INTEGER NOT NULL,
  cart_from_search_raw    INTEGER,
  order_from_search_raw   INTEGER,
  cart_conv_pct_raw       REAL,
  order_conv_pct_raw      REAL,
  is_rounded_pct          INTEGER NOT NULL DEFAULT 0,  -- 1 если WB округлил до 1%
  snapshot_date           INTEGER NOT NULL DEFAULT 0,
  ingested_at             INTEGER NOT NULL DEFAULT 0,
  ingest_run_id           INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, keyword, nm_id, period_start, period_end, snapshot_date)
);
CREATE INDEX ix_cmp_search_query_per_nm_nm ON cmp_search_query_per_nm(nm_id, keyword);

-- ────────────────────────────────────────────────────────────────────────────
-- 8. cmp_warehouse_metrics — warehouse динамика per nm
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_warehouse_metrics (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id  TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  nm_id          INTEGER NOT NULL,
  warehouse_name TEXT NOT NULL,
  metric_type    TEXT NOT NULL,            -- 'stock' / 'sales' / 'delivery'
  metric_value   INTEGER NOT NULL,
  period_start   INTEGER NOT NULL,
  period_end     INTEGER NOT NULL,
  snapshot_date  INTEGER NOT NULL DEFAULT 0,
  ingest_run_id  INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, nm_id, warehouse_name, metric_type, period_start, period_end, snapshot_date)
);

-- ────────────────────────────────────────────────────────────────────────────
-- 9. cmp_size_stocks — остатки по размерам (для категорий с sizes)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_size_stocks (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id TEXT NOT NULL REFERENCES cmp_groups(comparison_id),
  nm_id         INTEGER NOT NULL,
  size_name     TEXT NOT NULL,
  stock_count   INTEGER NOT NULL,
  date          INTEGER NOT NULL,
  ingest_run_id INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(comparison_id, nm_id, size_name, date)
);

-- ────────────────────────────────────────────────────────────────────────────
-- 10. cmp_dlq — Dead Letter Queue для failed purchases (для retry)
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE cmp_dlq (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  comparison_id TEXT NOT NULL,
  nm_ids_json   TEXT NOT NULL,
  error_msg     TEXT,
  retry_count   INTEGER NOT NULL DEFAULT 0,
  created_at    INTEGER NOT NULL,
  resolved_at   INTEGER,
  ingest_run_id INTEGER NOT NULL REFERENCES raw_runs(id)
);
CREATE INDEX ix_cmp_dlq_unresolved ON cmp_dlq(resolved_at) WHERE resolved_at IS NULL;

-- ────────────────────────────────────────────────────────────────────────────
-- 11. analytic_category_pool (OPTIONAL) — исторический пул конкурентов
-- ────────────────────────────────────────────────────────────────────────────
CREATE TABLE analytic_category_pool (
  id                 INTEGER PRIMARY KEY AUTOINCREMENT,
  my_nm_id           INTEGER,                          -- per-card (NULL → per-category)
  subject_id         INTEGER NOT NULL,
  competitor_nm_id   INTEGER NOT NULL,
  snapshot_date      INTEGER NOT NULL,
  source             TEXT NOT NULL,                    -- 'serp_main', 'serp_narrow', 'mpstats', 'db_earner'
  rank               INTEGER,
  is_top30           INTEGER NOT NULL DEFAULT 0,
  ingest_run_id      INTEGER NOT NULL REFERENCES raw_runs(id),
  UNIQUE(subject_id, competitor_nm_id, snapshot_date)
);
CREATE INDEX ix_analytic_category_pool_subject ON analytic_category_pool(subject_id, snapshot_date DESC);

-- ────────────────────────────────────────────────────────────────────────────
-- VIEWS
-- ────────────────────────────────────────────────────────────────────────────
-- Conversion ratios без хранения (decimal precision through computation)
CREATE VIEW v_cmp_funnel_with_conversions AS
SELECT
  cfd.*,
  CAST(add_to_cart_count AS REAL) / NULLIF(open_card_count, 0) AS cart_conv_pct,
  CAST(orders_count AS REAL) / NULLIF(open_card_count, 0) AS order_conv_pct,
  CAST(orders_count AS REAL) / NULLIF(add_to_cart_count, 0) AS cart_to_order_pct
FROM cmp_funnel_daily cfd;

-- Revenue 30-дневный rolling per nm
CREATE VIEW v_revenue_30d_per_nm AS
SELECT
  nm_id,
  SUM(orders_sum_kopeks) AS revenue_30d_kopeks,
  SUM(orders_count) AS orders_30d,
  CAST(SUM(orders_sum_kopeks) AS REAL) / 100 / 1e6 AS revenue_30d_mln_rub
FROM cmp_funnel_daily
WHERE date >= (strftime('%s', 'now', '-30 days'))
GROUP BY nm_id;

-- ────────────────────────────────────────────────────────────────────────────
-- Comments на PostgreSQL миграцию
-- ────────────────────────────────────────────────────────────────────────────
-- INTEGER → BIGINT (для PG для большой scale)
-- AUTOINCREMENT → GENERATED ALWAYS AS IDENTITY
-- strftime('%s', ...) → EXTRACT(EPOCH FROM ...)
-- WAL mode не нужен (PG own MVCC)
-- ON CONFLICT semantics одинаковая на SQLite/PG (psycopg2 supports)
