# Онбординг — Получение токенов, cookies, проверка прав

Это самый важный документ. Если онбординг сделан неправильно — pipeline не запустится. Агент: пройди по разделам **по порядку**, не пропускай checks.

## 0. Pre-requisites (проверить ДО setup)

Перед тем как тратить время на токены — убедись что у пользователя есть всё нужное:

### 0.1 WB Seller аккаунт

- ✅ Зарегистрирован как продавец на https://seller.wildberries.ru
- ✅ Есть **активные карточки** (хотя бы 1) — иначе не из чего извлекать narrow keywords
- ⚠ Если только **покупатель** — pipeline бесполезен (cabinet endpoint только для seller-аккаунтов)

**Как проверить:** агент спросит пользователя:
> Зайди на seller.wildberries.ru → видишь свои карточки в "Товары"? Сколько их?

### 0.2 WB Seller тариф

Cabinet «Сравнение карточек» **доступен на любом тарифе**, но:
- Лимит = ~1100 сравнений/мес (включая бесплатные refresh уже-купленных)
- `POST /api/analytics/v3/sales-funnel/products` (для ranking моих карточек по revenue) — **только Jam tier и выше**

**Как проверить sales-funnel доступ:**
```bash
curl -X POST https://seller-content.wildberries.ru/api/analytics/v3/sales-funnel/products \
  -H "Authorization: $WB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"period":{"start":"2026-04-21","end":"2026-05-20"},"limit":1}'
# 200 → Jam доступен ✓
# 403/404 → Jam tier не подключен ✗
```

**Если Jam недоступен:** ranking своих по revenue через alternative — aggregate `wb_orders + wb_sales` за 30 дней (см. [09-troubleshooting.md](09-troubleshooting.md#нет-jam-tier)).

### 0.3 MPStats subscription

- ✅ Аккаунт на https://mpstats.io
- ✅ Тариф включает **Analytics v1** endpoint (базовый Start обычно включает)
- ⚠ Free trial — обычно 7-14 дней, недостаточно для регулярного использования

**Как проверить tariff:** агент спросит:
> Зайди на mpstats.io → твой тариф называется как? Если "Free" / "Trial" — нужно upgrade.

### 0.4 Chrome browser на той же машине

Для `browser_cookie3` нужен **физический Chrome** с залогиненным WB кабинетом. Не Chromium, не Edge (хотя они тоже Chromium-based — теоретически работает, но непроверено).

**Как проверить:**
- macOS: `ls ~/Library/Application\ Support/Google/Chrome/Default/Cookies`
- Linux: `ls ~/.config/google-chrome/Default/Cookies`
- Windows: `dir %LOCALAPPDATA%\Google\Chrome\User Data\Default\Cookies`

Файл существует → ОК. Не существует → Chrome не установлен или другой профиль.

### 0.5 Permissions (macOS)

На macOS Cookies файл защищён Keychain — терминалу нужно дать **Full Disk Access**:
- System Settings → Privacy & Security → **Full Disk Access**
- Включить Terminal.app (или iTerm2, VS Code, что используешь)

Без этого `browser_cookie3` падает с `OperationalError: unable to open database file`.

### 0.6 Subject_id своей категории

**Subject_id** — это integer ID категории WB (`357` = Кремы, `358` = Шампуни, …). Тебе нужно знать subject_id той категории в которой ты продаёшь.

**Как найти:**

Вариант A — из URL твоей карточки:
```
https://www.wildberries.ru/catalog/12345678/detail.aspx?targetUrl=GP&subject_id=357
                                                                              ^^^
```

Вариант B — через Seller API content list (после онбординга):
```bash
curl -X POST https://content-api.wildberries.ru/content/v2/get/cards/list \
  -H "Authorization: $WB_API_TOKEN" \
  -d '{"settings":{"cursor":{"limit":1}}}' | jq '.cards[0].subjectID'
# → 357
```

Вариант C — список всех categories на WB:
```bash
curl https://content-api.wildberries.ru/content/v2/object/all \
  -H "Authorization: $WB_API_TOKEN" | jq '.data[] | "\(.subjectID): \(.subjectName)"'
```

**Запиши subject_id** в memory агента — он будет нужен на каждом запуске.

### 0.7 MPStats parent_category

MPStats группирует subject_id под **верхнюю категорию**. Тебе нужно знать какая верхняя для твоего subject:

| Твой subject включает | MPStats parent_category |
|---|---|
| Кремы, шампуни, маски, парфюмы — косметика | `Красота` |
| Платья, обувь, аксессуары | `Одежда` |
| Электроника, гаджеты | `Электроника` |
| Игрушки | `Детям` |
| Книги | `Книги` |
| Спорттовары | `Спорт` |

**Если не уверен** — открой mpstats.io → Categories tree → найди свой subject, путь сверху = parent_category.

Сохрани в `.env`:
```bash
MPSTATS_PARENT_CATEGORY=Красота
```

⚠ Параметр **case-sensitive**. "красота" (lowercase) НЕ работает.

## 1. WB Seller API Personal Token (180 дней)

### Что это

JWT-токен для всех Seller API endpoints (свои карточки, orders, sales, sales-funnel, stocks, prices).

### Получение

Агент должен сказать пользователю **точно эти шаги**:

1. Открой **https://seller.wildberries.ru** в Chrome
2. Залогинься (логин + SMS код если 2FA)
3. Правый верх — твой аватар → **"Доступ к API"**
4. Если ещё нет токенов — кнопка "Создать новый токен"
5. **Имя:** `wb-pool-cabinet` (любое осмысленное)
6. **Сроки:** 180 дней (максимум)
7. **Тип токена:** Personal (НЕ Test)
8. **Скоупы (отметь ВСЕ что нужны):**
   - ✅ Контент (карточки)
   - ✅ Статистика (orders / sales / stocks)
   - ✅ **Аналитика** ⚠️ для sales-funnel (Jam tier)
   - ✅ Цены и скидки
   - ✅ Поставки / Маркетплейс (опц., для FBO остатков)
9. Кнопка "Создать"
10. **СКОПИРУЙ JWT ПРЯМО СЕЙЧАС** — длинная строка `eyJhbGc...`. **Больше показан не будет**, только при первом создании.

### Сохранение

В `.env` проекта:
```bash
WB_API_TOKEN=eyJhbGciOiJIUzI1NiIs...полный_JWT_сюда...
```

**Безопасность:**
```bash
chmod 600 .env                    # доступ только владельцу
grep -F .env .gitignore || echo .env >> .gitignore
```

### Validation (canary)

После сохранения **обязательно** прогони canary:

```bash
# Простой ping — список своих карточек, limit 1
curl -X POST https://content-api.wildberries.ru/content/v2/get/cards/list \
  -H "Authorization: $WB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"settings":{"cursor":{"limit":1},"filter":{"withPhoto":-1}}}'
```

**Что должно быть:**
- HTTP **200** + JSON с `data.cards[]` (хотя бы 1 карточка)
- HTTP **401** → токен невалидный или истёк
- HTTP **403** → недостаточно scopes (Content не включён)

Также проверь Jam:
```bash
# Sales-funnel canary
TODAY=$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d 'yesterday' +%Y-%m-%d)
START=$(date -u -v-31d +%Y-%m-%d 2>/dev/null || date -u -d '31 days ago' +%Y-%m-%d)
curl -X POST https://seller-content.wildberries.ru/api/analytics/v3/sales-funnel/products \
  -H "Authorization: $WB_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"period\":{\"start\":\"$START\",\"end\":\"$TODAY\"},\"limit\":1}"
```

- 200 → Jam tier активен ✓
- 403/404 → Jam недоступен. Ranking своих надо делать через orders+sales aggregate

## 2. MPStats API Token

### Получение

1. Открой **https://mpstats.io** (зарегистрирован?)
2. Залогинься
3. Правый верх → твой email → **Settings → API**
4. Кнопка **"Generate token"** (или скопируй существующий — не показывается в plain text, только при создании)
5. Скопируй: формат `mps_xxxxxxxxxxxxxxxxxxxxxxxx`

### Сохранение

```bash
echo "MPSTATS_API_TOKEN=mps_xxx..." >> .env
```

### Validation

```bash
# Test через /category/items с минимальным запросом
YESTERDAY=$(date -u -v-1d +%Y-%m-%d 2>/dev/null || date -u -d 'yesterday' +%Y-%m-%d)
MONTH_AGO=$(date -u -v-31d +%Y-%m-%d 2>/dev/null || date -u -d '31 days ago' +%Y-%m-%d)
curl "https://mpstats.io/api/analytics/v1/wb/category/items?path=Красота&d1=$MONTH_AGO&d2=$YESTERDAY&startRow=0&endRow=1" \
  -X POST \
  -H "X-Mpstats-TOKEN: $MPSTATS_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"filterModel":{"subject_id":{"filterType":"number","type":"equals","filter":357}},"sortModel":[{"colId":"revenue","sort":"desc"}]}'
```

**Что должно быть:**
- HTTP **200** + JSON `{"data":[{"id":12345,...}]}` (1 карточка) — token OK, tariff OK
- HTTP **401** → token невалидный
- HTTP **402** → tariff не покрывает endpoint или ops лимит исчерпан
- HTTP **422** → ошибка params (проверь даты, parent_category, subject_id)

### Tracking ops

MPStats не возвращает remaining ops в headers (нет стандартного `X-RateLimit-Remaining`). Считай вручную или через mpstats.io dashboard. Каждый Discovery run = 3 ops.

## 3. WB Cabinet Cookies (для покупок)

### Почему отдельно от Seller API token

Cabinet endpoint `seller-content.wildberries.ru/ns/analytics-api/content-analytics/...` использует **сессионные cookies** (HttpOnly), не Bearer JWT. Разные endpoints, разные авторизации.

### Метод 1: browser_cookie3 (рекомендуется)

Самый стабильный способ — `browser_cookie3` читает cookies из обычной Chrome session.

#### Pre-checks

- ✅ Chrome установлен и пользователь залогинен в seller.wildberries.ru
- ✅ Full Disk Access для терминала (macOS, см. 0.5)
- ✅ Chrome ЗАКРЫТ или хотя бы все вкладки `seller.wildberries.ru` / `seller-content.wildberries.ru` закрыты (иначе SQLite Cookies файл locked)

#### Запуск

```bash
wb-pool setup-cookies
# Если Chrome Profile не "Default":
wb-pool setup-cookies --cookie-file ~/Library/Application\ Support/Google/Chrome/Profile\ 1/Cookies
```

Скрипт:
1. Читает cookies из Chrome SQLite файла
2. Фильтрует только `*.wildberries.ru`
3. Сохраняет в `data/cabinet_cookies.pkl` (pickle, binary)
4. Печатает: `OK: imported X cabinet cookies, oldest expires YYYY-MM-DD`

#### Метод 1.1: Если пользователь НЕ залогинен в Chrome

Агент должен помочь — открыть Chrome и попросить логин.

С computer-use MCP:
```python
# 1. Open Chrome to seller-content.wildberries.ru
await navigate("https://seller-content.wildberries.ru/competitor-comparison/")
# 2. Wait for login prompt
# 3. Ask user
print("Залогинься, пройди 2FA (если включено), кликни 'Сравнение карточек' страницу. Скажи когда готов.")
# 4. After user confirms → close Chrome → import cookies
```

Или без computer-use:
```
Агент: Открой Chrome → https://seller-content.wildberries.ru/competitor-comparison/
       Залогинься, пройди 2FA если есть, покликай UI 30 секунд (пройди по табам).
       ЗАТЕМ ЗАКРОЙ все вкладки с *.wildberries.ru.
       Готов?

Пользователь: yes

Агент: wb-pool setup-cookies
```

### Метод 2: headful Playwright (НЕ рекомендуется)

Альтернатива если browser_cookie3 не работает. Требует Playwright deps (~200 MB). См. [09-troubleshooting.md](09-troubleshooting.md).

### Метод 3: Прямой логин из кода (КАТЕГОРИЧЕСКИ не рекомендуется)

WB банит за подозрительные паттерны (multiple login attempts, отсутствие realistic browser fingerprint). Не делай этого.

### Validation

```bash
# Direct ping к limits endpoint
python -c "
import pickle, httpx
from pathlib import Path

cookies_path = Path('data/cabinet_cookies.pkl')
cookies = pickle.loads(cookies_path.read_bytes())
cookie_dict = {c.name: c.value for c in cookies if 'wildberries.ru' in c.domain}

r = httpx.get(
    'https://seller-content.wildberries.ru/ns/analytics-api/content-analytics/api/v2/competitor-comparison/limits',
    cookies=cookie_dict,
    headers={
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Accept': 'application/json',
    },
)
print(r.status_code, r.json() if r.status_code == 200 else r.text[:200])
"
```

**Что должно быть:**
- 200 + `{"available":1100,"used":0,...}` → cookies OK ✓
- 401 → cookies expired или невалидные. Re-import.
- 403 → пользователь не имеет доступа к feature (try-paid сервис?)

### Tracking expiry

Cabinet cookies живут ~1-2 недели inactivity. Агент должен:
- Помнить дату последнего import (memory key `cabinet_cookies_imported_at`)
- При 401 на cabinet API — **сразу** предложить re-import (не паниковать)

```python
# В коде:
try:
    response = await cabinet_client.post(...)
except CabinetAuthExpired:
    log.warning("cookies_expired", last_import=settings.cabinet_cookies_imported_at)
    print("Cookies expired. Закрой Chrome → wb-pool setup-cookies → запусти снова.")
    sys.exit(1)
```

## 4. 2FA / двухфакторка

Если у пользователя 2FA на WB:
- **Browser_cookie3 работает нормально** — он берёт уже-залогиненную сессию, 2FA пройдена в Chrome
- **API token** при создании запросит SMS код (через UI), но потом сам токен не зависит от 2FA
- **Cabinet cookies** обновляются при каждом логине — если пользователь не логинится через 2FA несколько недель, могут истечь

Рекомендация: пользователь должен раз в 2 недели зайти в Chrome → seller.wildberries.ru → подтвердить логин (2FA) → закрыть Chrome → `wb-pool setup-cookies`. Можно поставить cron-напоминание.

## 5. Финальный onboarding check

После всех 3 шагов запусти полный health-check:

```bash
wb-pool doctor

# Ожидается (все зелёные):
# Pre-requisites:
#   ✅ Python 3.11+
#   ✅ Chrome installed
#   ✅ Full Disk Access (macOS)
#
# Tokens:
#   ✅ WB Seller API:    OK (token expires 2026-12-31)
#   ✅ WB Seller Jam:    OK (sales-funnel доступен)
#   ✅ MPStats API:      OK (responds to test query)
#
# Cabinet:
#   ✅ Cookies imported: 14 cookies, oldest expires 2026-06-04
#   ✅ Cabinet limits:   1100/1100 available (0 used this month)
#
# Database:
#   ✅ Schema:           alembic head 0001 (latest)
#   ✅ WAL mode:         enabled
#
# Config:
#   ✅ MPSTATS_PARENT_CATEGORY: 'Красота'
#   ✅ MY_BRAND_MARKERS:        ['mybrand', 'моёназвание']
#   ⚠ Subject IDs in DB: 0    (запусти `wb-pool pull-cards` чтобы загрузить свои)
```

## 6. First-run smoke test (без cabinet расходов)

Прежде чем тратить cabinet слоты — прогони **discovery only** на тестовой категории:

```bash
# 1. Загрузи свои carto (Seller API content list)
wb-pool pull-cards
# → wb_cards: ~50-500 rows added

# 2. Загрузи MPStats keywords для своих
wb-pool pull-mpstats-keywords --subject-id 357
# → mpstats_keywords: ~1000-5000 rows added
# ⚠ Cost: 1 MPStats op (читаем keywords)

# 3. Discovery (0 cabinet, ~3 MPStats ops, ~25 SERP requests)
wb-pool discover --subject-id 357 --subject-name "Кремы" --pre-flight
# → показывает что будет делать

wb-pool discover --subject-id 357 --subject-name "Кремы"
# → ~30-60 сек wall-clock
# → data/pools/subject-357-<ts>.json создан
# → printout: "pool_size=268, A=98, B=153, C=53, duration=18.6s"
```

**Если всё прошло** — onboarding успешный. Можно переходить к buy + ingest.

**Если что-то упало** — см. [09-troubleshooting.md](09-troubleshooting.md) или re-run шаги выше.

## 7. Что должен запомнить агент

Сохрани **навсегда** (не теряй между сессиями):

```
✓ user_subject_ids: [357, 358, ...]
✓ user_brand_markers: ['mybrand', 'моёназвание']
✓ mpstats_parent_category: 'Красота'
✓ wb_api_token: stored in .env (180 days, expires YYYY-MM-DD)
✓ mpstats_api_token: stored in .env
✓ cabinet_cookies_path: data/cabinet_cookies.pkl
✓ cabinet_cookies_imported_at: YYYY-MM-DD HH:MM
✓ has_jam_tier: True / False
✓ chrome_profile: Default / Profile 1 / ...
```

В Claude Code это `bd remember --key=...`. В других AI агентах — своя memory система.

## 8. Безопасность

Финальный security check после onboarding:

```bash
# .env должен быть в .gitignore
grep -q "^\.env$" .gitignore || echo "❌ .env not in .gitignore!"

# Permissions
ls -l .env data/cabinet_cookies.pkl
# Должно быть -rw------- (600), не -rw-r--r--

# Никаких токенов в shell history
history | grep -i 'WB_API_TOKEN\|MPSTATS_API_TOKEN' && echo "❌ Tokens in shell history!"

# Никаких токенов в logs
grep -r 'eyJhbGc\|mps_' data/logs/ 2>/dev/null && echo "❌ Tokens in logs!"
```

## Checklist для агента — финальный

После онбординга проверь что все галочки стоят:

- [ ] WB Seller token получен, в `.env`, validated через canary, Jam tier подтверждён
- [ ] MPStats token получен, в `.env`, validated через canary, tariff достаточный
- [ ] Cabinet cookies импортированы, в `data/cabinet_cookies.pkl`, validated через /limits endpoint
- [ ] Subject_id своих категорий записан в memory
- [ ] MPSTATS_PARENT_CATEGORY правильный
- [ ] MY_BRAND_MARKERS в `.env`
- [ ] БД создана через `wb-pool migrate`
- [ ] `wb-pool pull-cards` прошёл → wb_cards заполнен
- [ ] `wb-pool pull-mpstats-keywords --subject-id X` прошёл → mpstats_keywords заполнен
- [ ] `wb-pool discover --pre-flight` показывает разумные числа
- [ ] `wb-pool discover` без --execute прошёл за <60 сек, output JSON создан
- [ ] `.env` permissions 600
- [ ] `.env` и `data/cabinet_cookies.pkl` в `.gitignore`

**Только после всех галочек** можно переходить к покупке → [05-purchase-pipeline.md](05-purchase-pipeline.md).

## Что дальше

→ [02-project-setup.md](02-project-setup.md) — структура проекта + deps
→ [04-discovery-workers.md](04-discovery-workers.md) — как работают 4 worker'а
