# Validation & Reporting

## Цель

После полного цикла (Discovery → Purchase → Excel) проверить:
1. Pool качественный — мало мусора, много sticky-tier
2. Алгоритм нашёл реальных топов (не пропустил кого-то)
3. Per-layer performance (какой слой лучше работает в этой категории)
4. Мои топ-карточки покрыты pool'ом (sanity check)

## Метрики качества

### Tier distribution

Каждую купленную карточку classify по revenue 30d:

| Tier | Revenue 30d | Что значит |
|---|---|---|
| **sticky** | ≥ 1M ₽ | Настоящий топ (≥ 12M ₽/год) |
| **mid** | 500k - 1M ₽ | Крепкий конкурент |
| **low** | 100k - 500k ₽ | Средний — может быть полезен для контекста |
| **trash** | < 100k ₽ | Слабый, скорее всего мусор |

Хорошее качество: **~30-40% sticky, ~20-25% mid, ~30-35% low, ~5-10% trash**.

### Per-layer trash

Какой слой дал больше слабых карточек:

```sql
-- 'Layer X only' = карточки нашёл только этот слой (никто другой)
SELECT
  layer,
  COUNT(*) AS total,
  SUM(CASE WHEN rev_30d < 50000000 THEN 1 ELSE 0 END) AS trash_under_500k,
  100.0 * SUM(CASE WHEN rev_30d < 50000000 THEN 1 ELSE 0 END) / COUNT(*) AS pct_trash
FROM (
  SELECT
    json_extract(layer_membership_json, '$[0]') AS layer,  -- если только один layer
    SUM(cfd.orders_sum_kopeks) AS rev_30d
  FROM pool_join cmp_funnel_daily cfd ON cfd.nm_id = pool_join.nm_id
  WHERE cfd.date >= :ps_epoch AND cfd.date <= :pe_epoch
  GROUP BY pool_join.nm_id
)
GROUP BY layer;
```

Типичное распределение (наш опыт):
| Слой only | trash % |
|---|---|
| Layer A (main SERP) | 10-15% |
| Layer B (narrow SERP) | 18-30% |
| Layer C (MPStats) | 3-10% ⭐ |
| Layer D (DB earners) | 0% |

### My carto coverage

Проверь: сколько твоих топ-20 по выручке оказалось в pool. Это **sanity check** — если 0/20, что-то пошло не так в Worker 2 (narrow keywords).

```python
async def check_my_coverage(
    *,
    engine: AsyncEngine,
    pool_nm_ids: set[int],
    subject_id: int,
    n_my_top: int = 20,
) -> dict[str, int]:
    """
    Compare pool with my top-N by revenue from Seller API sales-funnel.
    Returns: {'my_top_total': N, 'in_pool': M, 'missing': K}
    """
    settings = get_settings()
    token = settings.wb_api_token.get_secret_value()
    today = date.today() - timedelta(days=1)

    async with WBAPIClient.analytics(token=token) as client:
        fetcher = SalesFunnelFetcher(client)
        products = await fetcher.fetch_subject_products(
            subject_id=subject_id,
            period_start=today - timedelta(days=30),
            period_end=today,
        )
    my_top = sorted(products, key=lambda p: -p.order_sum_kopeks)[:n_my_top]
    my_nms = {p.nm_id for p in my_top}

    return {
        "my_top_total": len(my_nms),
        "in_pool": len(my_nms & pool_nm_ids),
        "missing": len(my_nms - pool_nm_ids),
        "missing_nm_ids": sorted(my_nms - pool_nm_ids),
    }
```

⚠ **Что значит «мои не в pool»?**

В каждой категории это может означать **разное**:

1. **Мои не топы категории.** Например, моя топ-2 делает 8.93M ₽/90d, а top-200 категории Кремы начинаются с 30-50M ₽/90d. Это норма — мы middle-tier игроки.

2. **Pool builder их пропустил.** Если у нас сильные карточки и они НЕ в pool — что-то не так в discovery (Layer A SERP не нашёл, Layer B по узкому ключу не сработал).

Норма: ожидаем 3-8 из топ-20 моих в pool (15-40%). Это значит **алгоритм работает**, но многие наши carto — реально middle-tier по всей категории.

Если 0/20 — bug в Worker 2 (narrow keywords не извлёкся или extractor выкинул всех).

### Top-N новых найденных

Самое интересное — какие **крупные** конкуренты нашлись:

```sql
SELECT
  nm_id,
  layer_sources_json,
  SUM(orders_sum_kopeks) / 100 / 1e6 AS rev_30d_mln
FROM cmp_funnel_daily cfd
JOIN pool_metadata pm ON pm.nm_id = cfd.nm_id
WHERE cfd.date >= :ps_epoch AND cfd.date <= :pe_epoch
  AND nm_id NOT IN (SELECT nm_id FROM previously_known)  -- только новые
GROUP BY nm_id
ORDER BY rev_30d_mln DESC
LIMIT 10;
```

Хороший pool находит карточки с **десятками миллионов ₽/мес** которых ты раньше не видел.

## CLI команда

```bash
wb-pool report --subject-id 357 --pool-json data/pools/subject-357-1779316531.json
```

Output:

```
=== ALGORITHM REPORT — Кремы (subject 357) ===

Pool: 268 nm (Layer A=98, B=153, C=53)

Tier distribution (revenue last 30d):
  ≥1M ₽  sticky:     93 (35%)   ⭐ настоящие топы
  500k-1M mid:       58 (22%)
  100k-500k low:     85 (32%)
  <100k  trash:      32 (12%)   ⚠ можно отфильтровать

Per-layer quality (% trash):
  Layer A only:     12% (8/68)
  Layer B only:     20% (28/142)  ← самый шумный
  Layer C only:      4% (2/45)    ⭐ самый чистый

My carto coverage:
  Top-20 моих:       4/20 в pool (20%)
  Missing: [...]     (это карточки которые не в top-200 категории по revenue)

Top-10 new (revenue 30d):
   1. nm=467642296   rev= 36.98 M₽  sources=[serp_narrow]
   2. nm=399134789   rev= 19.19 M₽  sources=[mpstats]
   3. nm=293473196   rev= 14.59 M₽  sources=[mpstats]
   ...

Сделано:
  - 1 sales-funnel call (мои top-20)
  - 25 SERP requests
  - 3 MPStats ops
  - 27 cabinet slots spent
  - 12,285 funnel rows added to DB
```

## Что делать если результат плохой

### Много мусора (>15% trash)

→ Подними `--feedbacks-threshold` (300 → 500 → 1000). Это отсекает слабые-но-ранжированные карточки.

### Мои не в pool (<10% из топ-20)

→ Проверь:
- Есть ли мои `wb_cards` в БД с `is_archived=0`? `SELECT * FROM wb_cards WHERE subject_id = X AND brand = 'MyBrand' AND is_archived = 0`
- Есть ли `mpstats_keywords` для них? `SELECT COUNT(*) FROM mpstats_keywords WHERE nm_id IN (...)`
- Если нет — запусти `wb-pool pull-cards` + `wb-pool pull-mpstats-keywords`

→ Если carto есть, но pool их не покрывает — твои carto **не в top-200 категории по revenue**. Это норма, не bug.

### Слишком много мусора в Layer B

→ Уменьши `--serp-narrow-top` (20 → 10 → 5)
→ Или подними feedbacks (300 → 1000)

### MPStats Layer C = 0

→ Проверь `MPSTATS_API_TOKEN`
→ Проверь tariff (нужен Analytics v1 plan)
→ Проверь `--mpstats-parent-category` правильный для subject

### Дубликаты или corrupted данные

→ `wb-pool db-doctor` — проверь schema
→ `SELECT COUNT(DISTINCT comparison_id) FROM cmp_funnel_daily` vs `SELECT COUNT(*) FROM cmp_groups WHERE status='ready'` — должно совпадать
→ Если не совпадает — какие-то покупки не доехали ETL. Используй `retry-dlq`.

## Continuous monitoring (для multiple categories)

```sql
-- Качество pool за последний месяц по всем категориям
SELECT
  cg.subject_id,
  COUNT(DISTINCT cg.comparison_id) AS groups,
  COUNT(DISTINCT cgm.nm_id) AS unique_nm,
  SUM(cfd.orders_sum_kopeks) / 100 / 1e6 AS total_revenue_mln,
  AVG(CASE WHEN cfd.orders_sum_kopeks >= 100000000 THEN 1.0 ELSE 0.0 END) AS sticky_share
FROM cmp_groups cg
JOIN ... ON cgm.comparison_id = cg.comparison_id
JOIN cmp_funnel_daily cfd ON cfd.comparison_id = cg.comparison_id
WHERE cg.created_at >= strftime('%s', 'now', '-30 days')
GROUP BY cg.subject_id;
```

Health indicator: `sticky_share` ≥ 30% означает pool builder работает корректно.

## Что дальше

→ [08-orchestrator.md](08-orchestrator.md) — оркестратор который связывает всё в один пайплайн
