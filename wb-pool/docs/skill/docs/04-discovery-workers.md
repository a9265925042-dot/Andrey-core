# Discovery Workers — 4 независимых добытчика

## Философия: «не пропустить топа»

Каждый worker — **самостоятельный добытчик**. Все применяют `feedbacks_threshold` фильтр независимо. Их outputs объединяются (union) и дедуплицируются. **Никакого composite score / sticky base / trim** — потому что любая такая логика может «выбить» хорошую карточку из одного слоя в пользу средней карточки из нескольких. Это плохо.

Mantra: **«если worker нашёл карточку — она гарантированно в pool».**

## Список workers

| # | Worker | Источник | Что находит | Стоимость |
|---|---|---|---|---|
| 1 | **SERP main** | curl_cffi → u-search.wb.ru | Top-N по категорийному запросу ("Кремы") | 1 SERP запрос |
| 2 | **SERP narrow** | curl_cffi → u-search.wb.ru | Top-N по узким ключам моих топ-карточек | N SERP запросов (N = ~10-20) |
| 3 | **MPStats 3 windows** | mpstats.io | Top-N по revenue в категории, 3 фиксированных окна | 3 MPStats ops |
| 4 | **DB earners** *(optional)* | local SQL | Cabinet-known earners ≥1M ₽/30d из `cmp_funnel_daily` | 0 (SQL only) |

## Worker 1: SERP main

### Что делает

Один запрос к WB Public SERP по subject_name (e.g. «Кремы»). Возвращает top-N карточек как WB их ранжирует на главной странице категории.

### Параметры

```python
async def fetch_serp_main_layer(
    *,
    subject_name: str,           # e.g. "Кремы"
    top_n: int,                  # 100 по умолчанию
    feedbacks_threshold: int,    # 300 по умолчанию
    max_retries: int = 2,        # backoff если 429
    retry_delay_sec: int = 20,
) -> tuple[dict[int, dict[str, Any]], int]:
    """
    Returns:
        nm_to_meta: {nm_id: {"rank": N, "feedbacks": M}}
        dropped: int  # сколько отсечено по feedbacks
    """
```

### Реализация

См. [code/algo_i.py](../code/algo_i.py) функция `fetch_serp_main_layer`.

Ключевые моменты:
- Использует **`curl_cffi`** с TLS-fingerprint `chrome116` (имитация настоящего браузера). Без этого WB возвращает 401/403.
- **Version cascade**: SERP endpoint имеет несколько API версий (v18, v17, v15, ...). Если v18 упал — пробуем v17, v15. Реализовано в `serp_client.py`.
- **Retry на пустой результат** (если 0 после feedbacks-фильтра): обычно это значит 429 throttle. Backoff 20 сек, до 2 retries.

### Особенности

- WB SERP отдаёт **до 100 карточек на page**. Для top-100 хватит 1 запроса. Если нужно больше — pagination через `page` query param.
- `feedbacks` приходит в response per nm — фильтруем сразу.
- WB иногда возвращает **разные карточки** на повторный запрос (некоторая случайность в ranking). Это норма, accept it.

### Когда не работает

- 429 на все версии → IP throttle. Подожди 10-15 мин.
- Empty response с status 200 → возможно subject_name unrecognized. Проверь spelling.

### Stat (на примере «Кремы», top-100 + fbs≥300)

- Возвращает ~80-100 unique
- ~5-10% мусора (<500k ₽/30d cabinet revenue если хочется измерить)
- ~25-45% sticky-tier (≥1M ₽/30d)

## Worker 2: SERP narrow (узкие ключи)

### Концепция

Главный insight: **категория «Кремы» — широкая**. SERP по «Кремы» вытаскивает только мега-карточки общего профиля. Но у каждой моей карточки есть **узкая ниша** ("крем воск от трещин", "крем вокруг глаз"), где конкуренты — другие. Без этого слоя теряем нишевых конкурентов.

### Алгоритм

#### Подготовительный шаг: извлечение narrow keywords из моих карточек

```python
# Для каждой моей топ-N карточки (sales-funnel /products):
# 1. Берём её title
# 2. Берём её candidate keywords из mpstats_keywords (фильтр: avg_position <= 20, traffic > 0, sorted by traffic DESC)
# 3. Подбираем ОДИН keyword где:
#    - есть main_phrase из title (первые 3 значимых токена из title) — preferred
#    - ИЛИ хотя бы один токен из title есть как word в keyword
#    - НЕ содержит brand markers (e.g. "mybrand")
#    - НЕ в blacklist одиночных generic слов ("крем", "шампунь" — слишком широкие)
# 4. Дедуп между карточками
# Результат: ~5-15 unique narrow keywords
```

Полная реализация: [code/title_kw_extractor.py](../code/title_kw_extractor.py) функция `pick_narrow_keyword`.

#### Discovery шаг

```python
async def fetch_serp_narrow_layer(
    *,
    narrow_kws: list[str],       # ~10-15 ключей от подготовительного шага
    top_n_per_kw: int,           # 20 по умолчанию (= serp_top // 5)
    feedbacks_threshold: int,    # 300
    concurrency: int = 5,        # Semaphore для anti-throttle
) -> tuple[dict[int, dict], int]:
    """
    Для каждого ключа: 1 SERP запрос top-N.
    Union с дедупом, capture min_rank если nm появилась в нескольких ключах.
    """
```

### Параметризация по размеру категории

| Категория | serp_top | serp_narrow_top | Layer B output |
|---|---|---|---|
| Широкая (Кремы, Одежда) | 100 | 20 | ~150-300 unique |
| Средняя (Шампуни) | 100 | 20 | ~50-100 unique |
| Узкая (Ниша) | 30 | 6 | ~20-50 unique |

### Stat

- На «Кремы» с 11 узкими ключами × top-20: ~150 unique после feedbacks-фильтра
- ~15-25% мусор <500k ₽/30d (это **самый шумный** слой!)
- ~25-35% sticky-tier ≥1M ₽/30d

### Почему Layer B самый шумный

SERP ранжирует по релевантности + feedbacks. Можно быть top-20 по «крем воск от трещин» и продавать всего 50k ₽/мес. Узкая ниша — мало конкурентов, поэтому даже слабые туда лезут.

Если хочешь **меньше шума** в Layer B — поднимай `--feedbacks-threshold` (300 → 1000+).

## Worker 3: MPStats 3 windows

### Что делает

3 запроса к `POST /category/items` MPStats для **3 фиксированных окон**:
1. **`mpstats_live`** — последние 30 дней (rolling window)
2. **`mpstats_june`** — фиксированное июнь 2025 (reference window для сезонности)
3. **`mpstats_year`** — фиксированный 2025 год (long-term top)

В каждом окне берёт top-N по revenue.

### Параметры

```python
async def fetch_mpstats_layer(
    *,
    token: str,
    subject_id: int,
    parent_category: str,         # "Красота", "Одежда"
    per_window: int,              # 25 по умолчанию (= serp_top // 4)
    feedbacks_threshold: int,     # 300 (применяется к MPStats comments field)
    period_start: date,
    period_end: date,
) -> tuple[dict[int, dict], int]:
```

### Особенности

- **3 окна — параллельно** через `asyncio.gather` (MPStats endpoint = другой хост, не conflicts с SERP).
- **comments = WB feedbacks** (MPStats называет это comments в endpoint response). Фильтр comments >= threshold.
- **3 MPStats ops** = 2% от месячного 150 budget. Не критично если запускать ≤ 1× в день.
- **`d2` (период end)** должен быть `<= today-1`. Жёсткое правило, иначе HTTP 422.

### Реализация

См. [code/mpstats_topup.py](../code/mpstats_topup.py) функция `fetch_mpstats_top_n_with_comments`.

### Stat

- 3 окна × top-25 = 75 max, после дедупа ~50-90 unique
- **~3-5% мусора** ⭐ **самый чистый слой** (MPStats сортирует по revenue → топ по revenue не может быть слабым)
- ~80-95% sticky-tier

### Почему 3 окна

| Окно | Что находит |
|---|---|
| live (last 30d) | Текущие топы — кто прямо сейчас продаёт |
| june 2025 | Sезонные топы лета (если категория зависит от сезона) |
| year 2025 | Долгосрочные топы — кто стабильно в топе весь год |

Union покрывает разные cohort'ы конкурентов. 1 окно (только live) пропустит сезонных лидеров.

### Когда worker возвращает []

- `token` is None → не настроен MPSTATS_API_TOKEN. Пропустить слой, warn в логе.
- `period_end > today-1` → 422, поймать, log, пустой результат.
- 429 throttle → MPStats редко throttle на 150 ops/mo лимите.
- Tariff limit достигнут → 402 Payment Required.

## Worker 4 (optional): DB earners

### Что делает

Если в `analytic_category_pool` уже накопился исторический пул для этого `subject_id`, и в `cmp_funnel_daily` есть funnel data — можно бесплатно (только SQL) добавить cabinet-known earners ≥1M ₽/30d.

```python
async def fetch_db_earners_layer(
    *,
    engine: AsyncEngine,
    subject_id: int,
    period_start: date,
    period_end: date,
    revenue_threshold_kopeks: int = 100_000_000,   # 1M ₽
) -> set[int]:
    """
    SELECT nm_id FROM cmp_funnel_daily WHERE nm_id IN (
        SELECT competitor_nm_id FROM analytic_category_pool WHERE subject_id = :sid
    ) AND date BETWEEN :ps AND :pe
    GROUP BY nm_id
    HAVING SUM(orders_sum_kopeks) >= :th
    """
```

### Когда использовать

- ✅ Если pool уже накапливался какое-то время (несколько недель/месяцев)
- ✅ Защищает от случайного пропуска проверенных earners когда WB SERP/MPStats не нашли их в текущем запуске
- ❌ Не использовать в **первый раз** запуск для категории (БД пустая)
- ❌ Не использовать если ты намеренно хочешь **freshly discovered** pool без legacy

### Stat

- Кол-во nm зависит от истории. Для новой категории — 0. Для накопленной — ~100-200.
- 0% мусора (потому что они уже проверены через cabinet данные)
- 100% sticky-tier по определению (фильтр ≥1M)

## Дедупликация и round to 5

После всех workers:

```python
# Union
pool_set = (
    set(serp_main_nms) |
    set(serp_narrow_nms) |
    set(mpstats_nms) |
    set(db_earners_nms)
)

# CardDetail filter: убрать удалённые на WB
card_details = await fetcher.fetch_batch(list(pool_set))
final_pool = [nm for nm in pool_set if nm in card_details]

# Optional: round to multiple of 5 (cabinet quantum)
n = len(final_pool) - (len(final_pool) % 5)
final_pool = final_pool[:n]
```

В нашем коде round to 5 делается **на этапе покупки** через padding (см. [05-purchase-pipeline.md](05-purchase-pipeline.md)), не на этапе discovery. Это потому что мы хотим знать **полный** pool, а padding для покупки — отдельная задача.

## Конфиг параметров

| Параметр | Default | Что регулирует |
|---|---|---|
| `--serp-top` | 100 | Глубина Layer A |
| `--serp-narrow-top` | auto = serp_top // 5 | Глубина Layer B per keyword |
| `--mpstats-per-window` | auto = serp_top // 4 | Глубина Layer C per window |
| `--feedbacks-threshold` | 300 | Минимум отзывов на всех слоях |
| `--n-my-cards` | 20 | Сколько моих карточек брать как seed для narrow keywords |
| `--period-days` | 30 | Окно для sales-funnel ranking + Layer C live |

Меньшие категории → меньшие значения:
```bash
wb-pool discover --subject-id 999 --subject-name "Узкая ниша" \
                 --serp-top 30                # → narrow_top=6, mpstats=7
                                              # → pool size ~50-100
```

## Что должен помнить агент

1. **Перед Discovery нужны мои карточки в БД** (`wb_cards`) — иначе Worker 2 не построит narrow keywords. Делается через `wb-pool pull-cards` (Seller API).
2. **Перед Worker 2 нужны `mpstats_keywords`** в БД — иначе extractor вернёт пустые candidates. Делается через `wb-pool pull-mpstats-keywords --subject-id X`.
3. **Если нет своих карточек в категории** — Worker 2 ничего не находит. Используй `--manual-top-nm-ids 12345,67890` чтобы вручную указать seed nm для narrow keywords.
4. **Layer A → потом Layer B + Layer C параллельно** — это helps с throttle (Layer A один-в-один запрос, не должен конкурировать с Layer B fan-out).

## Output JSON

После `wb-pool discover` в `data/pools/subject-<id>-<ts>.json`:

```json
{
  "pool_nm_ids": [12345, 67890, ...],
  "feedbacks_threshold_used": 300,
  "layer_a_serp_main_count": 98,
  "layer_b_serp_narrow_count": 153,
  "layer_c_mpstats_count": 53,
  "narrow_kws_used": ["крем воск от трещин", "крем вокруг глаз", ...],
  "layer_membership": {
    "12345": ["serp_main", "mpstats"],
    "67890": ["serp_narrow"],
    ...
  },
  "dropped_low_feedbacks_per_layer": {"serp_main": 2, "serp_narrow": 32, "mpstats": 1},
  "dropped_deleted_on_wb": 3,
  "duration_seconds": 18.6
}
```

Этот JSON — input для покупки (см. [05-purchase-pipeline.md](05-purchase-pipeline.md)).

## Что дальше

→ [05-purchase-pipeline.md](05-purchase-pipeline.md) — как купить cabinet сравнения по pool JSON
