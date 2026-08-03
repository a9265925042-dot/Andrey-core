# Troubleshooting & FAQ

## Onboarding проблемы

### `browser_cookie3` не находит cookies

**Симптом:** `wb-pool setup-cookies` возвращает 0 cookies.

**Причины:**
1. Chrome открыт → файл cookies locked
2. Chrome Profile не "Default" (например, "Profile 1")
3. macOS — cookies в Keychain, нужны permissions

**Решение:**
```bash
# 1. Закрой Chrome полностью (Cmd+Q)
# 2. Run script

# Если Profile разный
wb-pool setup-cookies --cookie-file ~/Library/Application\ Support/Google/Chrome/Profile\ 1/Cookies
```

### `browser_cookie3` permissions error на macOS

**Симптом:** `OperationalError: unable to open database file`

**Решение:** Дать терминалу/IDE Full Disk Access:
- System Settings → Privacy & Security → Full Disk Access
- Добавить Terminal.app / iTerm / VS Code

### Cabinet 403 после fresh cookies

**Симптом:** `setup-cookies` импортировал cookies, но `doctor --check cabinet` возвращает 403.

**Причины:**
1. Cookies были получены до того как user "ходил" по cabinet UI
2. WB cabinet требует "прогретой" сессии
3. У пользователя read-only акк

**Решение:**
1. Открой Chrome → https://seller-content.wildberries.ru/competitor-comparison/
2. Покликай в UI 30 секунд (статистика, страницы)
3. Re-run `wb-pool setup-cookies`

### MPStats 401

**Симптом:** Worker 3 возвращает empty + log `mpstats_window_failed: HTTP 401`.

**Решение:**
- Проверь `MPSTATS_API_TOKEN` правильный (без пробелов, кавычек)
- Перегенерируй token в mpstats.io если он от старого аккаунта
- Проверь tariff — Analytics v1 endpoint требует определённый plan

### MPStats 402 (Payment Required)

**Симптом:** `HTTP 402 Payment Required` от MPStats.

**Решение:**
- Месячный лимит ops исчерпан (150/мес на стандартном плане)
- Подожди следующего месяца ИЛИ upgrade tariff
- Учитывай: каждый Discovery run = 3 ops

## Discovery проблемы

### Layer A = 0 (SERP main пустой)

**Симптом:** В JSON output `layer_a_serp_main_count: 0` + log `serp_main_empty_retrying`.

**Причины:**
1. **Throttle 429** — WB ограничил наш IP (после многих запросов)
2. **Subject_name неправильное** — WB вернул empty SERP
3. **Все carto не прошли feedbacks-filter** — высокий threshold для маленькой категории

**Решение:**
```bash
# 1. Подождать throttle cooldown
sleep 600 && wb-pool discover ...

# 2. Проверить subject_name (особенно русские буквы)
wb-pool discover --subject-name "Кремы"  # правильно
# vs
wb-pool discover --subject-name "Kpemы"  # неправильно — латинское K
```

### Layer B = 0 (narrow SERP)

**Симптом:** В JSON `layer_b_serp_narrow_count: 0` + log `narrow_kws_used: []`.

**Причины:**
1. **Нет своих карточек** в категории (`wb_cards` пустая для subject)
2. **Нет `mpstats_keywords`** для моих nm — extractor вернул empty candidates
3. **Brand markers слишком жёсткие** — все keywords отфильтрованы

**Решение:**
```bash
# 1. Pull свои carto
wb-pool pull-cards

# 2. Pull MPStats keywords для них
wb-pool pull-mpstats-keywords --subject-id 357

# 3. Проверь brand markers в .env
# MY_BRAND_MARKERS=mybrand,моёназвание
# Если слишком много вариантов — снижаем

# 4. Если своих НЕТ в этой категории — manual seed
wb-pool discover --subject-id 999 --subject-name "Новая ниша" \
  --manual-top-nm-ids 12345,67890,...   # карточки конкурентов чьи keywords хочешь использовать
```

### Layer C = 0 (MPStats пустой)

**Симптом:** `layer_c_mpstats_count: 0`.

**Причины:**
1. `MPSTATS_API_TOKEN` не настроен — log `mpstats_layer_skipped_no_token`
2. Все 3 окна вернули 422 — log `mpstats_window_failed` × 3
3. `subject_id` неправильный для MPStats (отличается от WB)

**Решение:**
```bash
# 1. Проверь token
wb-pool doctor --check mpstats

# 2. Проверь mpstats_parent_category
# Default "Красота". Для одежды → "Одежда", для электроники → "Электроника"
wb-pool discover ... --mpstats-parent-category "Одежда"

# 3. Subject ID может быть -1 для некоторых WB категорий в MPStats
# В этом случае фильтр не работает. Проверь по MPStats UI.
```

## Purchase проблемы

### `CabinetAuthExpired: 401 Unauthorized`

**Симптом:** Перед или во время покупки — 401.

**Решение:**
- Re-run `wb-pool setup-cookies`
- Проверь что Chrome не logged out
- Если упорно 401 — pull cookies из Chrome incognito session (после fresh login)

### `429 Too Many Requests` во время cabinet purchase

**Симптом:** Несколько групп подряд возвращают 429.

**Решение:**
- WB cabinet throttle. Подожди 5-10 минут
- Или уменьши batch size — покупай по 5-10 групп за раз
- Запусти `--max-batch-size 5` (если поддерживается твоей версией)

### Cabinet `limits.availableWithoutCharge` пуст

**Симптом:** Каждая повторная покупка той же группы списывает слот (вместо 0).

**Причина:** Это **редкий bug на стороне WB** — иногда idempotency не работает.

**Решение:**
- Не паникуй, потерь минимальные если группа уже была куплена (refresh free)
- Если систематически — открой ticket в WB support
- Workaround: храни `cmp_id` локально и проверяй наличие до покупки

### Funnel rows = 0 после успешной покупки

**Симптом:** `cmp_id` создан, status='ready', но `cmp_funnel_daily` пуст для этой группы.

**Причины:**
1. ETL не доехал (response пустой `dayDynamics`)
2. Парсинг сломался (поменялся формат WB JSON)
3. UPSERT conflict не сработал

**Решение:**
```sql
-- Проверь raw payload (если хранится)
SELECT * FROM cmp_groups WHERE comparison_id = 'sub357-...';

-- Manually retry ETL для группы
wb-pool retry-etl --comparison-id sub357-g12345-p20260219-20260520
```

## Excel ingest проблемы

### Download status="failed"

**Симптом:** `POST /file-manager/download` возвращает downloadId, потом `GET /downloads` показывает status="failed".

**Причина:** WB не смог сгенерировать XLSX (внутренняя ошибка их side)

**Решение:**
- Подожди 10 мин, retry
- Если упорно failed — open ticket WB support

### XLSX парсинг падает (calamine)

**Симптом:** `CalamineError: Unsupported sheet format`.

**Решение:**
- Auto-fallback к openpyxl должен сработать. Проверь логи: `openpyxl_fallback_used`.
- Если оба не справились — формат файла кривой, опубликуй issue в GitHub

### `is_rounded_pct=1` для всех rows

**Симптом:** Все conversion % в БД помечены как rounded.

**Причина:** WB exported `cart_conv_pct` и `order_conv_pct` с 1% precision (норма).

**Решение:** не использовать `*_conv_pct_raw` для точных расчётов. Вычисляй через `cart_from_search_raw / open_card_count` напрямую — это **точные int** числа.

## Validation проблемы

### Pool слишком маленький (<50 nm)

**Причины:**
1. Категория узкая → норма
2. Feedbacks threshold слишком высокий → понижай (300 → 100)
3. Discovery worker не отработал (см. выше)

### % мусора >25%

**Причины:**
- Layer B (narrow) слишком глубокий → уменьшай `--serp-narrow-top`
- Threshold слишком низкий → поднимай

### Мои не в pool вообще (0/20)

**Причины:**
1. У меня нет своих в этой категории → норма (но Worker 2 = 0)
2. Все мои feedbacks < threshold → проверь
3. Discovery не находит реально хорошие carto → bug, разбирайся

## DB проблемы

### Database locked

**Симптом:** `sqlite3.OperationalError: database is locked`

**Причины:**
1. Несколько процессов пишут одновременно
2. SQLite Journal mode = DELETE (default) — медленнее

**Решение:**
- Enable WAL mode: `PRAGMA journal_mode=WAL;` в alembic env.py
- Use semaphore в коде для concurrent writes
- Не запускай 2× `wb-pool` параллельно

### Foreign key constraint failed

**Симптом:** При INSERT в `cmp_funnel_daily` — FK error на `comparison_id`.

**Причина:** ETL вставляет funnel rows **до** того как `cmp_groups` row создан.

**Решение:** Insert `cmp_groups` first в той же UoW. См. seed_cmp_group_from_payload — оно должно идти до upsert_funnel_daily.

### Index missing → slow queries

**Симптом:** Запросы вдруг стали медленные (10× медленнее раньше)

**Решение:**
```bash
# Check indexes
sqlite3 data/wb-pool.db "SELECT name FROM sqlite_master WHERE type='index'"

# Re-apply migrations
uv run alembic upgrade head

# Manually create missing index
sqlite3 data/wb-pool.db "CREATE INDEX ix_cmp_funnel_nm_date ON cmp_funnel_daily(nm_id, date)"
```

## Performance / cost проблемы

### Cabinet budget исчерпывается слишком быстро

**Причины:**
1. Запускаешь discovery + buy слишком часто
2. Pool слишком большой (>200 nm)
3. Categorisации не diff с already-bought

**Решение:**
- Запускай 1× в неделю на категорию, не каждый день
- Pool 100-200 nm достаточно для big-picture
- Проверь diff работает: `wb-pool buy --dry-run` показывает только новые

### MPStats budget исчерпывается

**Причины:**
- 3 ops × N запусков = 3N ops/мес
- 150 ops = 50 запусков max
- Если >5 категорий × 1 раз/нед × 4 нед × 3 ops = 60 ops — OK
- Если запускаешь много → tariff upgrade

### WB SERP throttle постоянно

**Причины:**
- Запускаешь 10+ дискавери в час
- Один IP, много категорий параллельно

**Решение:**
- Не более 2-3 discoveries в час
- Sequential между категориями
- Если production scale — proxy rotation (СЛОЖНО, не делай если не разбираешься)

## Что делать когда ничего не помогает

1. Подними детальное логирование: `LOG_LEVEL=DEBUG` в `.env`
2. Запусти с `--pre-flight` чтобы увидеть план без trat'ы ресурсов
3. Изолируй проблему: попробуй один шаг отдельно (`wb-pool discover` без buy)
4. Check git log нашего проекта на похожие commits — там описаны similar issues
5. Открой issue в GitHub репо (если он есть) с воспроизводимым минимальным примером

## Что должен помнить агент

1. **Cookies — самая частая проблема.** При первой ошибке cabinet — всегда подозревай cookies expired.
2. **WB throttle — глобально per-IP.** Если упорно 429 — подожди, не пытайся обойти.
3. **MPStats budget — это hard cap.** 150 ops = 50 запусков. Считай заранее.
4. **`--pre-flight` всегда первый шаг.** Не покупай без проверки плана.
5. **DLQ — друг.** Failed groups в DLQ можно retry без потерь.
