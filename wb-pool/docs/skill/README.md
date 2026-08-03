# WB Cabinet Purchases — Skill для AI агента

**Production-ready пайплайн для покупки сравнений Wildberries.** Прошёл боевые испытания, валидирован на реальных категориях.

## Что это

End-to-end skill для AI агента (Claude Code / любой другой) который автоматизирует:

1. **Онбординг** — получение токенов (WB Seller API, MPStats, Cabinet cookies)
2. **Discovery** — 4 независимых добытчика конкурентов (SERP main + SERP narrow keywords + MPStats 3 окна + DB earners)
3. **Покупка cabinet сравнений** — группы 5 nm + atomic per-group purchase + DLQ
4. **Excel ingest** — keywords + warehouse + size stocks в БД (бесплатно)
5. **Validation** — отчёт по качеству pool

Архитектура: **3-4 независимых слоя**, никакого composite score / sticky base / trim. Mantra: «не пропустить топа». Все слои применяют feedbacks filter независимо, union + дедуп.

## Для кого

- Селлер на Wildberries с собственными карточками
- Хочет автоматизировать **competitive intelligence**: кто реально топы в твоей категории, какие у них keywords, конверсии, склады, остатки
- Готов потратить ~30-100 cabinet слотов на категорию (из месячного бюджета 1100)

## Что в этом репозитории

```
wb-cabinet-purchases-skill/
├── SKILL.md                    # ⭐ START HERE — главный skill file (superpowers формат)
├── README.md                    # этот файл
├── docs/                        # детальные гайды по фазам
│   ├── 01-onboarding.md         # токены + cookies
│   ├── 02-project-setup.md      # Python / uv / deps
│   ├── 03-database.md           # схема БД, 8 таблиц, principles
│   ├── 04-discovery-workers.md  # 4 worker'а
│   ├── 05-purchase-pipeline.md  # покупка + ETL
│   ├── 06-excel-ingest.md       # Excel парсинг + ingest
│   ├── 07-validation.md         # отчёт качества
│   ├── 08-orchestrator.md       # связь всего
│   └── 09-troubleshooting.md    # частые проблемы
├── code/                        # готовый Python код (Apache-2.0)
│   ├── algo_i.py                # discovery orchestrator + 4 workers
│   ├── algo_i_types.py          # Config + Result dataclasses
│   ├── title_kw_extractor.py    # narrow keywords из моих карточек
│   ├── serp_with_feedbacks.py   # SERP клиент с feedbacks
│   ├── mpstats_topup.py         # MPStats 3 windows fetcher
│   ├── cli_discover.py          # CLI: discovery only
│   ├── cli_buy.py               # CLI: buy by pool JSON
│   └── cli_ingest_excel.py      # CLI: Excel ingest
├── templates/
│   ├── .env.example             # ENV variables
│   ├── pyproject.toml           # Python deps (uv + pip совместимо)
│   └── .gitignore               # safe defaults
└── schemas/
    └── db.sql                   # full SQL schema (8 tables + views)
```

## Quick start

### 1. Дать этот skill своему AI агенту

Распакуй папку куда удобно (например `~/.claude/skills/wb-cabinet-purchases/`) и попроси агента прочитать `SKILL.md`. Дальше агент пройдёт через docs/ по порядку.

### 2. Минимальный flow для пользователя

```bash
# Setup проекта
mkdir wb-pool && cd wb-pool
cp -r ~/Desktop/wb-cabinet-purchases-skill/code/* src/wb_pool/
cp ~/Desktop/wb-cabinet-purchases-skill/templates/* ./
uv sync

# Configure (заполни токены)
cp .env.example .env
$EDITOR .env

# Onboarding (агент должен помочь)
wb-pool setup-cookies        # импорт из Chrome
wb-pool doctor                # validation

# Init DB
wb-pool migrate

# Pull свои carto + MPStats keywords
wb-pool pull-cards
wb-pool pull-mpstats-keywords --subject-id 357

# Discovery (бесплатно, без слотов)
wb-pool discover --subject-id 357 --subject-name Кремы

# Buy + Excel ingest (тратит слоты)
wb-pool buy --json data/pools/subject-357-<ts>.json --execute
wb-pool ingest-excel --subject-id 357

# Validation report
wb-pool report --subject-id 357
```

Или одной командой:
```bash
wb-pool run-category --subject-id 357 --subject-name Кремы
# → preflight → discover → diff → confirm → buy → excel → report
```

## Стек

- **Python 3.11+** (для `int | None` syntax, `match` statements)
- **httpx** + **curl_cffi** (TLS-fingerprint для WB SERP)
- **browser_cookie3** (импорт cookies из Chrome без логина в коде)
- **SQLAlchemy 2.0 async** + **alembic** (БД)
- **typer** + **rich** (CLI)
- **structlog** (structured JSON logging)
- **python-calamine** (быстрый Excel parser) + openpyxl (fallback)

## Что в коде

Готовый production-ready Python код всего пайплайна. Cleaned up — без Semily-specific вещей (наш проект, на основе которого сделан skill). Универсальный для любого WB селлера.

См. `code/` для всех модулей.

## Что НЕ покрывается

- Создание / редактирование собственных карточек (нужны другие WB Seller API endpoints)
- Управление ценами / акциями / promotions
- WB advertising campaigns
- Парсинг чужих фото / отзывов

Для этого нужны отдельные модули.

## Архитектурные принципы

1. **TDD строго** для testable parts. R&D orchestrators — smoke via live runs.
2. **Append-only БД.** Никаких DELETE. UPSERT с UNIQUE на natural key + snapshot_date.
3. **Idempotency.** Все ETL handlers переживают повторный запуск.
4. **Per-source bulkhead.** Один сломанный источник не валит весь pipeline.
5. **Pre-flight everywhere.** Перед тратой ресурсов — показать оператору план, ждать `y`.
6. **Все timestamps INT epoch UTC.** Все цены INT копейки. Никаких FLOAT для денег.

## Disclaimer

Этот пайплайн использует public/cabinet/MPStats endpoints Wildberries и mpstats.io. Соблюдай их Terms of Service. Не обходи rate limits через VPN / прокси.

WB иногда меняет API без предупреждения. Если что-то сломалось — проверь recent changes WB cabinet (изменения формата JSON / новые поля / переименования endpoints).

## License

MIT. Free для commercial и personal use. Без warranty.
