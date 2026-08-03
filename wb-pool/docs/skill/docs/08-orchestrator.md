# Оркестратор — Full pipeline в одной команде

## Что такое оркестратор

Связь всех workers + покупка + ingest + validation в одну команду:

```bash
wb-pool run-category --subject-id 357 --subject-name Кремы
```

8 фаз последовательно с stop-points для оператора:

```
[1] Preflight
    ↓ (tokens valid? cookies fresh? budget OK?)
[2] Pull my cards + MPStats keywords if stale
    ↓
[3] Discovery (4 workers)
    ↓ (pool ready, output JSON)
[4] Diff with already-purchased
    ↓
[5] STOP → show pre-flight, wait for `y`
    ↓ (user confirmed)
[6] Purchase saga (per group try/except, DLQ)
    ↓
[7] Excel ingest (free for new cmp_id)
    ↓
[8] Validation report
```

## Зачем

- **Одна команда** делает всё. Не нужно помнить порядок шагов.
- **Stop-point** перед тратой слотов — защита от accidents.
- **Resume after crash** — каждый шаг идемпотентный, можно перезапустить с того же subject_id.
- **Логирование** через structlog — потом разобрать что когда пошло не так.
- **Reporting** — оператор сразу видит результат покупки.

## Реализация

См. полный код [code/orchestrator.py](../code/orchestrator.py).

Skeleton:

```python
async def run_category_pipeline(
    *,
    engine: AsyncEngine,
    config: OrchestratorConfig,
) -> OrchestratorResult:
    """End-to-end pipeline for one category."""
    log = get_logger(__name__).bind(subject_id=config.subject_id)

    # --- Phase 1: Preflight ---
    log.info("preflight_start")
    pre = await preflight_check(engine=engine, config=config)
    if not pre.all_ok:
        log.error("preflight_failed", details=pre.errors)
        raise PipelineAbort("preflight failed", details=pre.errors)
    log.info("preflight_ok", cabinet_slots=pre.cabinet_slots_avail, mpstats_ops=pre.mpstats_ops_avail)

    # --- Phase 2: Refresh my data (idempotent) ---
    if config.pull_cards_first:
        log.info("pull_my_cards_start")
        await pull_my_cards(engine, config.subject_id)
        log.info("pull_my_cards_done")

    if config.pull_mpstats_keywords:
        log.info("pull_mpstats_keywords_start")
        await pull_mpstats_keywords(engine, config.subject_id)
        log.info("pull_mpstats_keywords_done")

    # --- Phase 3: Discovery ---
    log.info("discovery_start")
    pool_result = await run_discovery(
        engine=engine,
        discovery_config=AlgorithmIConfig(
            subject_id=config.subject_id,
            subject_name=config.subject_name,
            serp_top=config.serp_top,
            feedbacks_threshold=config.feedbacks_threshold,
            ...
        ),
    )
    log.info(
        "discovery_done",
        pool_size=len(pool_result.pool_nm_ids),
        layer_a=pool_result.layer_a_serp_main_count,
        layer_b=pool_result.layer_b_serp_narrow_count,
        layer_c=pool_result.layer_c_mpstats_count,
    )

    # Write pool to JSON for evidence
    pool_json_path = _OUTPUT_DIR / f"subject-{config.subject_id}-{int(time.time())}.json"
    pool_json_path.write_text(json.dumps(asdict(pool_result), ensure_ascii=False, indent=2))

    # --- Phase 4: Diff with already-purchased ---
    new_nm, already_bought = await diff_with_purchased(
        pool=pool_result.pool_nm_ids,
        engine=engine,
    )
    groups = chunk_with_padding(new_nm, padding_pool=already_bought)
    log.info("purchase_plan", groups=len(groups), new_nm=len(new_nm), already_bought=len(already_bought))

    if not groups:
        log.info("no_new_groups_to_buy")
        # Still do excel ingest for refresh
        await ingest_excel_for_subject(engine, config.subject_id, config.period_start, config.period_end)
        return OrchestratorResult(pool_size=len(pool_result.pool_nm_ids), bought=0, refreshed=len(already_bought))

    # --- Phase 5: STOP → confirm with operator ---
    if config.require_confirmation:
        _print_purchase_plan(plan=groups, subject_name=config.subject_name)
        if not _confirm_with_operator():
            log.warning("aborted_by_operator")
            return OrchestratorResult(aborted=True)

    # --- Phase 6: Purchase saga ---
    purchase_result = await purchase_saga(
        engine=engine,
        cookies=load_cookies(),
        groups=groups,
        subject_id=config.subject_id,
        period_start=config.period_start,
        period_end=config.period_end,
    )
    log.info("purchase_done", purchased=purchase_result.purchased, failed=purchase_result.failed)

    # --- Phase 7: Excel ingest (free for newly-bought cmp_id) ---
    log.info("excel_ingest_start")
    excel_result = await ingest_excel_for_subject(
        engine=engine,
        subject_id=config.subject_id,
        period_start=config.period_start,
        period_end=config.period_end,
    )
    log.info("excel_ingest_done", cmp_ingested=excel_result.cmp_count, rows=excel_result.rows_written)

    # --- Phase 8: Validation report ---
    report = await validation_report(
        engine=engine,
        pool_nm_ids=set(pool_result.pool_nm_ids),
        subject_id=config.subject_id,
        new_nm_ids=set(new_nm),
    )
    _print_report(report)
    log.info("pipeline_done", **report.summary())

    return OrchestratorResult(
        pool_size=len(pool_result.pool_nm_ids),
        bought=purchase_result.purchased,
        failed=purchase_result.failed,
        excel_ingested=excel_result.cmp_count,
        validation=report,
    )
```

## Конфиг

```python
@dataclass(frozen=True)
class OrchestratorConfig:
    # Required
    subject_id: int
    subject_name: str

    # Discovery
    serp_top: int = 100
    serp_narrow_top: int | None = None         # auto = serp_top // 5
    mpstats_per_window: int | None = None      # auto = serp_top // 4
    feedbacks_threshold: int = 300
    n_my_cards: int = 20

    # Purchase
    period_days: int = 90                      # cabinet comparison period
    require_confirmation: bool = True          # show pre-flight, wait for `y`

    # Pre-pulls
    pull_cards_first: bool = True              # ensure wb_cards fresh
    pull_mpstats_keywords: bool = True         # ensure mpstats_keywords fresh

    # Cabinet category mapping
    mpstats_parent_category: str = "Красота"
```

## CLI flags

```bash
wb-pool run-category \
  --subject-id 357 \
  --subject-name "Кремы" \
  --serp-top 100 \
  --feedbacks-threshold 300 \
  --period-days 90 \
  --mpstats-parent-category "Красота"

# Skip pre-pulls (если уверен данные свежие)
wb-pool run-category --subject-id 357 --subject-name "Кремы" \
  --no-pull-cards --no-pull-mpstats

# Discovery only (без покупки)
wb-pool run-category --subject-id 357 --subject-name "Кремы" --discover-only

# Auto-confirm (для cron, опасно)
wb-pool run-category --subject-id 357 --subject-name "Кремы" --auto-confirm
```

## Errors и recovery

### При ошибке в Phase 1 (Preflight)

- Cookies expired → reset via `wb-pool setup-cookies`
- Cabinet budget exhausted → ждать след. месяц
- WB API token expired → новый из cabinet UI

### При ошибке в Phase 3 (Discovery)

- 429 throttle WB SERP → подожди 10 мин, retry
- MPStats 402 (tariff) → нужно upgrade plan
- 0 carto на WB → check subject_id correctness

### При ошибке в Phase 6 (Purchase)

- Per group try/except — failed cmp идут в DLQ
- После batch — `wb-pool retry-dlq` для retry failed
- Если все 27 групп упали — что-то с cookies/IP, расследуй

### При ошибке в Phase 7 (Excel ingest)

- 404 на download → cmp_id ещё не готов на WB. Подожди пару минут, retry.
- Excel parse error → calamine не справился. Fallback к openpyxl автоматический.

## Логирование

Все события через structlog:

```python
log.info("event_name", key1=value1, key2=value2)
# → {"event": "event_name", "key1": "value1", "key2": "value2", "timestamp": "...", "level": "info"}
```

JSON формат позволяет:
- `grep` по конкретным event names
- Aggregate metrics из логов (`jq`)
- Send в ELK / Grafana если нужен monitoring

Ключевые events:
- `preflight_start` / `preflight_ok` / `preflight_failed`
- `discovery_start` / `discovery_done`
- `purchase_plan` (с details — сколько групп)
- `purchase_group_ok` / `purchase_group_failed`
- `excel_ingest_done`
- `pipeline_done` (с summary)

## Time estimates

Полный run на одну категорию (Кремы-size, ~30 групп):

```
Phase 1: Preflight           ~3 sec
Phase 2: Pull cards          ~5-10 sec (если нужно)
Phase 2b: Pull mpstats kw    ~20-30 sec (если нужно, 20+ requests)
Phase 3: Discovery           ~30-60 sec (Algorithm I)
Phase 4: Diff                <1 sec (SQL)
Phase 5: Confirm             pause for user
Phase 6: Purchase (30 grps)  ~2-3 min
Phase 7: Excel ingest        ~3-5 min
Phase 8: Validation          ~5 sec
─────────────────────────────────────
Total: ~5-10 min wall-clock + 30 cabinet slots
```

## Параллельный запуск нескольких категорий

```python
# Параллельно (НЕ рекомендую — может пересечься throttle)
results = await asyncio.gather(*[
    run_category_pipeline(engine=engine, config=cfg)
    for cfg in [config_kremy, config_shampoo, ...]
])

# Sequential (рекомендую — медленнее, но стабильнее)
for cfg in [config_kremy, config_shampoo, ...]:
    await run_category_pipeline(engine=engine, config=cfg)
```

⚠ **Throttle:** WB SERP throttle = global per-IP. Параллельный запуск 3-5 категорий = 100+ SERP requests одновременно = почти гарантировано 429.

Sequential или интервалы — безопаснее.

## Cron / scheduled runs

```bash
# crontab или launchd
0 3 * * * cd /path/to/wb-pool && uv run wb-pool run-category \
  --subject-id 357 --subject-name Кремы --auto-confirm \
  >> data/logs/cron.log 2>&1
```

⚠ **`--auto-confirm` опасен**. Используй только если ты на 100% уверен:
- Pool не вырастет в 10× (e.g. категория стабильна)
- Cabinet budget хватит на estimated слоты
- DLQ retry настроен

Лучшая практика — еженедельный manual run через `wb-pool run-category --subject-id ...` с интерактивной confirmation.

## Что должен помнить агент

1. **Pre-pulls идемпотентны.** Можно запускать `pull-cards` и `pull-mpstats-keywords` хоть каждый день. Это API call на Seller / MPStats, не cabinet.
2. **Discovery бесплатна.** 0 cabinet слотов. Можно запускать сколько хочешь. Стоит 3 MPStats ops.
3. **Diff важен.** Без diff будут двойные покупки. Diff делается через `cmp_funnel_daily` lookup.
4. **Confirmation важен.** Не пропускай. Даже если оператор раздражается каждый раз — это safety net.
5. **DLQ — для production reliability.** Для маленьких pools редко нужен. Для больших (>50 групп) обязателен.

## Что дальше

→ [code/orchestrator.py](../code/orchestrator.py) — полная реализация
→ [09-troubleshooting.md](09-troubleshooting.md) — частые проблемы
