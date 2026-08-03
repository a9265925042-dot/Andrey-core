# Bootstrap — пошаговый setup за 30-60 минут

Этот документ — **прямые команды** для AI агента который начинает с нуля. Прочитай SKILL.md сначала, потом следуй этому документу.

## Pre-check (2 минуты)

```bash
python3.11 --version  # Должно быть 3.11+
which uv || curl -LsSf https://astral.sh/uv/install.sh | sh
ls ~/Library/Application\ Support/Google/Chrome/Default/Cookies 2>/dev/null && echo "Chrome OK" || echo "Chrome не установлен или другой профиль"
```

## Phase 1: Project scaffold (5 минут)

```bash
# Создай папку проекта (рядом с этим skill)
mkdir wb-pool && cd wb-pool

# Скопируй templates
SKILL=~/Desktop/wb-cabinet-purchases-skill
cp $SKILL/templates/.env.example .env
cp $SKILL/templates/pyproject.toml ./
cp $SKILL/templates/.gitignore ./

# Создай структуру src/
mkdir -p src/wb_pool/{discovery,models,clients,purchase,etl}
touch src/wb_pool/{__init__.py,config.py,logging_setup.py,db.py,cli.py}
for dir in discovery models clients purchase etl; do
  touch src/wb_pool/$dir/__init__.py
done

# Создай data директории
mkdir -p data/{pools,cabinet/excel,logs}

# Init git (но НЕ git add yet — нужно настроить .gitignore)
git init -q
git add .gitignore && git commit -m "chore: initial gitignore" -q

# Setup deps
uv sync
```

## Phase 2: Copy provided code (3 минуты)

```bash
# Discovery layer (готов к использованию)
cp $SKILL/code/algo_i.py src/wb_pool/discovery/
cp $SKILL/code/algo_i_types.py src/wb_pool/discovery/
cp $SKILL/code/title_kw_extractor.py src/wb_pool/discovery/
cp $SKILL/code/serp_with_feedbacks.py src/wb_pool/discovery/
cp $SKILL/code/mpstats_topup.py src/wb_pool/discovery/mpstats_layer.py

# CLI обёртки
cp $SKILL/code/cli_discover.py src/wb_pool/
cp $SKILL/code/cli_buy.py src/wb_pool/
cp $SKILL/code/cli_ingest_excel.py src/wb_pool/

# Адаптационный гайд
cp $SKILL/code/STRUCTURE.md ./CODE_STRUCTURE.md
```

## Phase 3: Реализуй STUB модули (20-30 минут)

Используй псевдокод из [code/STRUCTURE.md](code/STRUCTURE.md). Минимум для запуска **discovery**:

1. **`src/wb_pool/config.py`** (5 min) — Pydantic Settings из .env
2. **`src/wb_pool/logging_setup.py`** (3 min) — structlog wrapper
3. **`src/wb_pool/db.py`** (5 min) — `create_engine` + `UnitOfWork`
4. **`src/wb_pool/clients/serp.py`** (10 min) — SERPClient с version cascade + curl_cffi
5. **`src/wb_pool/clients/wb_seller.py`** (5 min) — Bearer JWT HTTP client
6. **`src/wb_pool/clients/sales_funnel.py`** (5 min) — SalesFunnelFetcher
7. **`src/wb_pool/clients/card_detail.py`** (5 min) — CardDetail batch fetcher
8. **`src/wb_pool/models/`** ORM models (см. `schemas/db.sql`) (15 min)

Для buy + ingest добавь:
9. **`src/wb_pool/clients/cabinet.py`** + **`cookies.py`** (10 min)
10. **`src/wb_pool/purchase/groups.py`** (5 min)
11. **`src/wb_pool/etl/funnel.py`** (10 min)
12. **`src/wb_pool/etl/excel.py`** (15 min)

После реализации:
```bash
uv run mypy --strict src
uv run ruff check src
```

## Phase 4: Onboarding (15 минут)

Подробно см. [docs/01-onboarding.md](docs/01-onboarding.md). Кратко:

```bash
# 1. WB Seller token
# Открой seller.wildberries.ru → Доступ к API → создай Personal token с scopes
# (Content + Statistics + Analytics + Prices + Marketplace)
$EDITOR .env  # paste WB_API_TOKEN

# 2. MPStats token
# Открой mpstats.io → Settings → API → Generate
$EDITOR .env  # paste MPSTATS_API_TOKEN

# 3. Заполни остальное
$EDITOR .env  # MY_BRAND_MARKERS, MPSTATS_PARENT_CATEGORY

# 4. Permissions
chmod 600 .env

# 5. Cabinet cookies — Chrome ДОЛЖЕН БЫТЬ ЗАКРЫТ
wb-pool setup-cookies
# OR альтернатива: python -m wb_pool.cli setup-cookies

# 6. Validation
wb-pool doctor
# Все 3 канала должны быть зелёными
```

## Phase 5: Database init (3 минуты)

```bash
# Создай alembic config
uv run alembic init alembic
# Адаптируй alembic/env.py — добавь:
#   from wb_pool.models import Base
#   target_metadata = Base.metadata

# Создай initial migration из ORM models
uv run alembic revision --autogenerate -m "initial schema"

# Apply
uv run alembic upgrade head

# Verify
sqlite3 data/wb-pool.db ".tables"
# Ожидаемые tables: raw_runs, wb_cards, cmp_groups, cmp_funnel_daily,
#                   cmp_search_queries, cmp_search_query_per_nm,
#                   cmp_warehouse_metrics, cmp_size_stocks, mpstats_keywords
```

## Phase 6: First-run smoke test (10 минут)

```bash
# 1. Pull свои carto
wb-pool pull-cards
sqlite3 data/wb-pool.db "SELECT COUNT(*) FROM wb_cards"  # >0

# 2. Pull MPStats keywords для своих
wb-pool pull-mpstats-keywords --subject-id YOUR_SUBJECT_ID
sqlite3 data/wb-pool.db "SELECT COUNT(*) FROM mpstats_keywords"  # >0
# ⚠ 1 MPStats op

# 3. Discovery only (pre-flight = 0 cost)
wb-pool discover \
  --subject-id YOUR_SUBJECT_ID \
  --subject-name "YOUR_CATEGORY_NAME" \
  --pre-flight
# Должно показать что будет делать (без выполнения)

# 4. Реальный discovery (0 cabinet, 3 MPStats ops)
wb-pool discover \
  --subject-id YOUR_SUBJECT_ID \
  --subject-name "YOUR_CATEGORY_NAME"
# Output: data/pools/subject-YOUR_ID-<ts>.json
# Layer A=~98, Layer B=~100-300 в зависимости от широты ниши, Layer C=~30-90
```

**Если всё прошло** — продолжай:

```bash
# 5. Diff с already-bought (= 0 при первом запуске)
wb-pool buy \
  --json data/pools/subject-YOUR_ID-<ts>.json \
  --subject-id YOUR_SUBJECT_ID --subject-name "YOUR_NAME"
# Покажет план покупки. БЕЗ --execute — это dry-run.

# 6. ⚠ Реальная покупка (тратит cabinet слоты!)
# Перед --execute: убедись что план ОК, бюджет хватает
wb-pool buy \
  --json data/pools/subject-YOUR_ID-<ts>.json \
  --subject-id YOUR_SUBJECT_ID --subject-name "YOUR_NAME" \
  --execute

# 7. Excel ingest (бесплатно для купленных cmp_id)
wb-pool ingest-excel \
  --subject-id YOUR_SUBJECT_ID \
  --period-start 2026-02-21 \
  --period-end 2026-05-21

# 8. Validation report
wb-pool report --subject-id YOUR_SUBJECT_ID
```

## Phase 7: Productionize (опционально, +1-2 часа)

- **Logging:** `LOG_JSON=1` в `.env` + log rotation
- **Cron:** еженедельный `wb-pool run-category` через launchd / cron
- **Backup:** ежедневный dump SQLite в безопасное место
- **Monitoring:** alerts на failed runs (через logs или DLQ count)
- **Tests:** `pytest` unit tests для STUB модулей (хотя бы happy path)

## Acceptance criteria

После полного bootstrap должно работать:

```bash
# Health check
wb-pool doctor
# → all green

# One-shot pipeline для одной категории
wb-pool run-category --subject-id 357 --subject-name "Кремы"
# → preflight → discovery → diff → confirm 'y' → buy → excel → report
# → 100% полезных данных, 0 errors, <10 min wall-clock
```

## Если что-то не работает

См. [docs/09-troubleshooting.md](docs/09-troubleshooting.md). Самые частые проблемы:
- Cookies истекли → re-run `wb-pool setup-cookies`
- 429 от WB SERP → подожди 10-15 мин
- 422 от MPStats → check `d2 <= today-1`, `mpstats_parent_category` case-sensitive
- Empty `wb_cards` → запусти `wb-pool pull-cards` first

## Total time estimate

| Phase | Time |
|---|---|
| Pre-check | 2 min |
| Phase 1 Scaffold | 5 min |
| Phase 2 Copy code | 3 min |
| Phase 3 Реализация STUB | 20-30 min |
| Phase 4 Onboarding | 15 min |
| Phase 5 DB init | 3 min |
| Phase 6 Smoke test | 10 min |
| **Total до first buy** | **~1 час** |
| Phase 7 Productionize | +1-2 hr |

## Хороший момент остановиться

После Phase 6 (smoke test discovery only — 0 cabinet) можно **остановиться** и подумать:
- Pool качественный?
- Мои топы покрыты?
- Готов ли тратить cabinet слоты?

И только потом — buy + excel.

⚠ **Не пропускай этот gap.** Это spasает от случайной траты 30+ слотов на плохой pool.
