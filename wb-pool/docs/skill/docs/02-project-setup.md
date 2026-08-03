# Настройка проекта

## Системные требования

- **Python 3.11+** (для `int | None` syntax, `match` statements, etc.)
- **macOS / Linux** (Windows работает но не тестировался)
- **2GB RAM** свободно (SQLite + curl_cffi sessions)
- **~500MB диска** для зависимостей + данные

## Установка зависимостей

Рекомендуется **uv** (быстрее pip):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh   # install uv
mkdir wb-pool && cd wb-pool
cp ~/Desktop/wb-cabinet-purchases-skill/templates/pyproject.toml ./
cp ~/Desktop/wb-cabinet-purchases-skill/templates/.env.example ./.env
uv sync                          # install deps
```

Альтернатива (pip):

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r templates/requirements.txt
```

## Структура проекта

```
wb-pool/
├── .env                          # секреты (gitignored)
├── .env.example                  # template (committed)
├── .gitignore
├── pyproject.toml                # uv/pip config
├── alembic.ini                   # БД миграции config
├── data/
│   ├── wb-pool.db                # SQLite БД (gitignored)
│   ├── cabinet_cookies.pkl       # cookies dump (gitignored)
│   ├── pools/                    # JSON outputs дискавери (committed if нужны как evidence)
│   ├── cabinet/                  # cabinet JSON + Excel ZIPs (gitignored — приватные данные)
│   └── logs/                     # structlog JSON logs (gitignored)
├── src/wb_pool/
│   ├── __init__.py
│   ├── cli.py                    # typer CLI (wb-pool команды)
│   ├── config.py                 # settings (Pydantic Settings)
│   ├── db.py                     # SQLAlchemy engine + UoW
│   ├── models/                   # ORM models
│   ├── workers/                  # 4 discovery worker'а
│   │   ├── serp_main.py
│   │   ├── serp_narrow.py
│   │   ├── mpstats.py
│   │   └── db_earners.py
│   ├── orchestrator.py           # связь workers + покупка + ingest
│   ├── purchase/                 # cabinet purchase saga
│   │   ├── client.py             # WBCabinetClient (httpx + cookies)
│   │   ├── buy.py                # buy_groups
│   │   └── excel.py              # Excel parser + ingest
│   ├── mpstats/                  # MPStats client
│   ├── serp/                     # SERP client (curl_cffi)
│   └── wb_api/                   # Seller API client
├── tests/
│   ├── unit/
│   └── integration/
├── alembic/
│   ├── versions/                 # migrations
│   └── env.py
└── docs/
```

## Конфигурация (.env)

См. [templates/.env.example](../templates/.env.example).

Минимум для запуска:

```bash
WB_API_TOKEN=eyJhbGc...              # Seller API JWT (180d)
MPSTATS_API_TOKEN=mps_xxx            # MPStats Analytics v1
DATABASE_URL=sqlite+aiosqlite:///./data/wb-pool.db
MY_BRAND_MARKERS=mybrand_lowercase,второе_написание  # для фильтра keyword candidates
```

Опционально:
```bash
LOG_LEVEL=INFO                       # DEBUG/INFO/WARNING/ERROR
LOG_JSON=1                           # structured JSON логи (production)
WB_CABINET_COOKIES_PATH=./data/cabinet_cookies.pkl
MPSTATS_PARENT_CATEGORY=Красота      # верхняя категория для path param
```

## База данных (alembic init)

```bash
# Создать БД и применить миграции
uv run wb-pool migrate

# Альтернативно через alembic напрямую
uv run alembic upgrade head
```

Это создаст `data/wb-pool.db` (SQLite WAL) с 8 таблицами.

Подробнее о схеме — [03-database.md](03-database.md).

## Проверка установки

```bash
uv run wb-pool doctor
# Ожидается:
# ✅ Python:          3.11.7
# ✅ DB schema:       alembic head 0001 (latest)
# ✅ WB Seller API:   OK (token expires 2026-12-31)
# ✅ MPStats API:     OK (130/150 ops remaining)
# ✅ WB Cabinet:      OK (1100/1100 slots available)
# ✅ Dependencies:    all OK
```

Если что-то красным — следуй инструкциям из [01-onboarding.md](01-onboarding.md).

## Тесты (для разработки)

```bash
uv run pytest -q                     # быстрые unit (respx HTTP моки)
uv run pytest tests/integration --run-live    # live integration (требует API budget)

# Quality gates перед коммитом
uv run mypy --strict src
uv run ruff check src tests
```

## Логирование

Используется `structlog` с structured JSON:

```python
import structlog
log = structlog.get_logger(__name__)

log.info("pool_built", subject_id=357, pool_size=300, layer_a=98, layer_b=170, layer_c=32)
# Outputs: {"event": "pool_built", "subject_id": 357, ...}
```

Логи идут в `data/logs/wb-pool.jsonl` если `LOG_JSON=1`, иначе stderr с цветами.

Для production setup сделай log rotation через `logrotate` или Python `RotatingFileHandler`.

## Что дальше

После настройки → [03-database.md](03-database.md) (схема БД) → [04-discovery-workers.md](04-discovery-workers.md) (как работают workers).
