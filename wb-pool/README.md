# wb-pool — WB Cabinet Purchases pipeline

Рабочее пространство по скиллу **wb-cabinet-purchases** (полная копия скилла — в
[docs/skill/](docs/skill/)). Автоматизирует competitive intelligence для селлера
Wildberries: discovery конкурентов (4 независимых слоя) → покупка cabinet
сравнений (группы 5 nm) → Excel ingest (keywords / склады / остатки) →
validation report.

## Статус

Скилл поставляет готовый Discovery-код и контракты для остальных модулей
(см. [CODE_STRUCTURE.md](CODE_STRUCTURE.md)). Здесь реализовано всё:

| Модуль | Источник | Статус |
|---|---|---|
| `discovery/` (algo_i + 4 workers) | ✅ provided by skill | as-is, brand markers из `.env` |
| `config.py`, `logging_setup.py`, `db.py` | ⚠ STUB → реализовано | готово |
| `models/` (12 таблиц + 2 views) | ⚠ STUB → реализовано | схема = `docs/db.sql` |
| `clients/` (serp, wb_seller, sales_funnel, card_detail, cabinet, cookies) | ⚠ STUB → реализовано | готово |
| `purchase/groups.py` (diff, chunk+padding, atomic buy) | ⚠ STUB → реализовано | готово |
| `etl/funnel.py`, `etl/excel.py`, `etl/pull_cards.py`, `etl/mpstats_keywords.py` | ⚠ STUB → реализовано | готово |
| `cli.py` (12 команд, включая `run-category` оркестратор) | собран из cli_*.py + новые | готово |
| Тесты (23, offline) | новые | `uv run pytest` зелёный |

Проверено: `pytest` (23 passed), `ruff check` clean, `mypy --strict` clean.

⚠ **Что требует live-валидации при первом запуске** (в этой среде нет токенов
и доступа к WB):

- **Cabinet endpoints** (`clients/cabinet.py`) — пути `/ns/analytics-api/...`
  взяты из docs скилла; WB меняет их без предупреждения. При 404 сверь с
  DevTools в кабинете.
- **MPStats by_keywords** (`etl/mpstats_keywords.py`) — endpoint не описан в
  скилле; реализован tolerant-парсер + `--debug-dump` для снятия реального
  формата.
- **Excel layouts** (`etl/excel.py`) — парсеры листов по документированным
  заголовкам; при 0 rows после ingest смотри реальные заголовки в сохранённом
  ZIP (`data/cabinet/excel/`).

## Quick start

```bash
cd wb-pool
uv sync --extra dev

cp .env.example .env && chmod 600 .env
$EDITOR .env          # WB_API_TOKEN, MPSTATS_API_TOKEN, MY_BRAND_MARKERS, ...

uv run wb-pool migrate
uv run wb-pool setup-cookies      # Chrome закрыт, залогинен в seller-content.wildberries.ru
uv run wb-pool doctor             # все каналы зелёные?

uv run wb-pool pull-cards
uv run wb-pool pull-mpstats-keywords --subject-id 357

# Discovery — бесплатно (0 cabinet слотов, 3 MPStats ops)
uv run wb-pool discover --subject-id 357 --subject-name "Кремы" --pre-flight
uv run wb-pool discover --subject-id 357 --subject-name "Кремы"

# Покупка — dry-run по умолчанию; --execute тратит слоты НЕОБРАТИМО
uv run wb-pool buy --json data/pools/subject-357-<ts>.json \
  --subject-id 357 --subject-name "Кремы"
uv run wb-pool buy --json data/pools/subject-357-<ts>.json \
  --subject-id 357 --subject-name "Кремы" --execute

uv run wb-pool ingest-excel --subject-id 357 \
  --period-start 2026-05-04 --period-end 2026-08-02
uv run wb-pool report --subject-id 357 --subject-name "Кремы" \
  --pool-json data/pools/subject-357-<ts>.json

# Или всё одной командой (8 фаз, stop-point перед тратой слотов):
uv run wb-pool run-category --subject-id 357 --subject-name "Кремы"
```

## Правила безопасности (краткая выжимка из SKILL.md)

1. Каждая покупка новой группы 5 nm = **1 cabinet слот, необратимо** (бюджет ~1100/мес).
2. Всегда сначала dry-run / `--pre-flight`; live-покупка требует ввода `yes`.
3. MPStats числовые поля не для аналитики — реальные revenue из cabinet funnel.
4. MPStats `d2` ≤ вчера (иначе HTTP 422) — зашито в код.
5. Cookies живут ~1-2 недели → `wb-pool setup-cookies` при 401.
6. `.env` и `data/` не коммитятся (см. `.gitignore`).

Полные правила, disclaimer и troubleshooting — [docs/skill/](docs/skill/).

## Разработка

```bash
uv run pytest            # 23 offline-теста (parsers, grouping, ETL idempotency)
uv run ruff check src tests
uv run mypy src          # strict
```
