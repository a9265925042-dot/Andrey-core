# Excel Ingest — keywords + warehouse + size_stocks

## Контекст

После покупки `cmp_id` через `POST /competitor-comparison/nms` ты получил funnel data (revenue, orders, views per day per nm). Но cabinet хранит **ещё больше**: keywords с conversions, warehouse динамику, остатки по размерам. Эти данные доступны через Excel экспорт.

**Excel — бесплатный** после покупки cmp_id (любой загрузка XLSX не списывает слотов).

## Flow

```
1. POST /file-manager/download
   Body: {"cmpID": "sub357-g123456-p20260219-20260520", "type": "comparison-full"}
   Response: { "downloadId": "abc-def-ghi" }

2. (Polling) GET /file-manager/downloads
   Response: [
     {"downloadId": "abc-def-ghi", "status": "in_progress"},  # → wait + retry
     {"downloadId": "abc-def-ghi", "status": "ready", "url": "https://..."}
   ]

3. GET <url> → ZIP с XLSX внутри
4. Parse XLSX (calamine first, openpyxl fallback)
5. ETL → UPSERT в 4 таблицы
```

Total wait per cmp_id: ~10-30 сек (WB генерирует XLSX лениво).

## Что в Excel

ZIP содержит файлы:
- `comparison_<cmpID>.xlsx` — keywords + funnel breakdown
  - Sheet "Сводка" — общая статистика per nm
  - Sheet "Поисковые запросы" — keywords с per-nm conversions
  - Sheet "Динамика" — daily dynamics
- `warehouses_<cmpID>.xlsx` — warehouse metrics
  - Sheet "Склады" — per warehouse × per nm volumes
- `stocks_<cmpID>.xlsx` (если категория с размерами) — size stocks

## Parser

### Calamine (быстрый, default)

```python
from python_calamine import CalamineWorkbook

def parse_keywords_xlsx_calamine(path: Path) -> list[dict]:
    wb = CalamineWorkbook.from_path(path)
    sheet = wb.get_sheet_by_name("Поисковые запросы")
    rows = sheet.to_python()
    # rows[0] — header
    header = rows[0]
    return [dict(zip(header, row)) for row in rows[1:]]
```

Calamine в 10× быстрее openpyxl. Default для production.

### Openpyxl (fallback)

```python
from openpyxl import load_workbook

def parse_keywords_xlsx_openpyxl(path: Path) -> list[dict]:
    wb = load_workbook(path, read_only=True, data_only=True)
    sheet = wb["Поисковые запросы"]
    header = [c.value for c in next(sheet.iter_rows())]
    return [dict(zip(header, [c.value for c in row])) for row in sheet.iter_rows(min_row=2)]
```

Используется если calamine не справился (редко, на старых XLSX форматах).

## ETL: parse → DB

### Keywords (главное)

```python
async def ingest_keywords_xlsx(
    session: AsyncSession,
    *,
    xlsx_path: Path,
    comparison_id: str,
    period_start_epoch: int,
    period_end_epoch: int,
    ingest_run_id: int,
):
    rows = parse_keywords_xlsx(xlsx_path)

    # Each row: keyword, nm_id, frequency, freq_dynamics,
    #            cart_from_search, order_from_search, cart_conv_pct, order_conv_pct
    snapshot_epoch = int(datetime.now(UTC).timestamp())

    # Group 1: cmp_search_queries (per-keyword aggregate)
    keywords_data = group_by_keyword(rows, ...)
    await upsert_cmp_search_queries(session, keywords_data, ingest_run_id, snapshot_epoch)

    # Group 2: cmp_search_query_per_nm (per-keyword × per-nm metrics)
    per_nm_data = ...
    await upsert_cmp_search_query_per_nm(session, per_nm_data, ingest_run_id, snapshot_epoch)
```

⚠ **Внимание про `is_rounded_pct`:**

Excel выгружает conversion percentages с **1% precision** (округлено WB). Если `cart_conv_pct_raw = 10.0`, это может быть **9.5%-10.5%** на самом деле. Для точных расчётов используй `cart_from_search_raw / open_card_count` напрямую — это int divsion, точно.

Флаг `is_rounded_pct` в БД row для downstream consumers:
```sql
INSERT INTO cmp_search_query_per_nm (..., is_rounded_pct, ...)
VALUES (..., 1, ...);  -- WB округлил, не доверять процентам
```

### Warehouse

```python
async def ingest_warehouse_xlsx(...):
    """
    Each row: warehouse_name, nm_id, metric_type, metric_value, period_start, period_end
    UPSERT in cmp_warehouse_metrics
    """
```

### Size stocks

```python
async def ingest_size_stocks_xlsx(...):
    """
    Each row: nm_id, size_name, stock_count, date
    UPSERT in cmp_size_stocks
    """
```

## Полная реализация

См. [code/excel_ingest.py](../code/excel_ingest.py).

## CLI

```bash
# По specific cmp_id list
wb-pool ingest-excel \
  --comparison-id sub357-g12345-p20260219-20260520 \
  --comparison-id sub357-g67890-p20260219-20260520

# Все cmp_groups для subject + period
wb-pool ingest-excel \
  --subject-id 357 \
  --period-start 2026-02-19 \
  --period-end 2026-05-20

# Все недавно купленные (полезно после buy команды)
wb-pool ingest-excel --since-ingest-run-id 207
```

## Idempotent

`ingest-excel` идемпотентен:
- WB при повторном `POST /file-manager/download` для существующего cmp_id возвращает тот же download
- UPSERT в DB (ON CONFLICT DO UPDATE) — не дублирует rows

Можно повторно запускать после crash или для refresh данных.

## Время выполнения

| Cmp_id (groups) | Wall-clock |
|---|---|
| 1 | ~5-15 сек |
| 10 | ~50-100 сек |
| 27 | ~3-5 мин |
| 100 | ~10-20 мин |

Bottleneck — polling WB file-manager (XLSX генерируется ленно). Параллелизация не помогает (WB throttle).

## Output

После полного ingest:
```sql
-- Сколько keywords для одной группы
SELECT COUNT(*) FROM cmp_search_queries WHERE comparison_id = '...';
-- → ~500-2000 unique keywords

SELECT COUNT(*) FROM cmp_search_query_per_nm WHERE comparison_id = '...';
-- → ~500-3000 (keyword × nm)

SELECT COUNT(*) FROM cmp_warehouse_metrics WHERE comparison_id = '...';
-- → ~300-600 (warehouses × nm × metric_types)
```

## Что можно сделать с этими данными

После ingest у тебя есть **полная картина** для каждой купленной группы:

1. **Top keywords по которым конкурент продаёт:**
   ```sql
   SELECT keyword, SUM(order_from_search_raw) AS orders
   FROM cmp_search_query_per_nm
   WHERE nm_id = 12345
   GROUP BY keyword
   ORDER BY orders DESC LIMIT 10;
   ```

2. **Сравнить мою конверсию vs конкурент:**
   ```sql
   SELECT nm_id,
          AVG(cart_conv_pct_raw) AS avg_cart_conv,
          AVG(order_conv_pct_raw) AS avg_order_conv
   FROM cmp_search_query_per_nm
   WHERE comparison_id = '...'
     AND is_rounded_pct = 0  -- только точные значения
   GROUP BY nm_id;
   ```

3. **Where конкуренты держат склад:**
   ```sql
   SELECT warehouse_name, SUM(metric_value) AS total
   FROM cmp_warehouse_metrics
   WHERE nm_id = 12345 AND metric_type = 'stock'
   GROUP BY warehouse_name
   ORDER BY total DESC;
   ```

## Что дальше

→ [07-validation.md](07-validation.md) — как проверить что pool качественный
