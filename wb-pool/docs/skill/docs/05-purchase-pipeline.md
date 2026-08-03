# Purchase Pipeline — Покупка сравнений + ETL

## Контекст

После Discovery у тебя есть pool из ~150-500 unique nm. Чтобы получить funnel data (revenue, orders, conversions) — нужно купить cabinet сравнения через WB cabinet API. Каждая группа из 5 nm = 1 cabinet слот. Месячный лимит ~1100 слотов.

## Принципы

1. **Atomic per group.** Один POST `/competitor-comparison/nms` за группу из 5 nm. Либо вся группа куплена (slot списан), либо ошибка (slot не тронут).
2. **Idempotent.** Та же группа (sorted 5 nm + period) куплена дважды → второй раз 0 слотов через `/limits.availableWithoutCharge`.
3. **Per-group try/except.** Один failure не валит весь batch. Failed cmp идут в DLQ.
4. **Pre-flight first.** Перед `--execute` показать оператору точные числа, ждать `y`.
5. **Diff с уже купленным.** Если nm уже есть в `cmp_funnel_daily` для этого subject — пропустить (не покупать снова).

## Шаг 1: Diff с existing data

```python
async def diff_with_purchased(
    pool: list[int],
    *,
    engine: AsyncEngine,
) -> tuple[list[int], list[int]]:
    """
    Returns: (new_nm_to_buy, already_have_funnel_data)
    """
    async with engine.connect() as conn:
        placeholders = ",".join([f":n{i}" for i in range(len(pool))])
        params = {f"n{i}": nm for i, nm in enumerate(pool)}
        res = await conn.execute(text(
            f"SELECT DISTINCT nm_id FROM cmp_funnel_daily WHERE nm_id IN ({placeholders})"
        ), params)
        already = {int(row[0]) for row in res}
    new = [nm for nm in pool if nm not in already]
    return new, sorted(already & set(pool))
```

⚠ **Важно:** lookup делается через `cmp_funnel_daily` напрямую (не через `analytic_category_pool`). Это потому что мы покупаем именно по nm которые в pool, независимо от того в каком production-pool они исторически были.

## Шаг 2: Группировка по 5 с padding

```python
def chunk_with_padding(
    targets: list[int],
    padding_pool: list[int],
    *,
    group_size: int = 5,
    rng_seed: int = 42,
) -> list[list[int]]:
    """
    Split targets into groups of group_size. Last group < group_size — pad
    from padding_pool. Padding nm — те что мы уже купили раньше (refresh
    данных бесплатно).
    """
    rng = random.Random(rng_seed)
    available_padding = list(padding_pool)
    rng.shuffle(available_padding)
    pad_iter = iter(available_padding)

    groups: list[list[int]] = []
    for i in range(0, len(targets), group_size):
        chunk = list(targets[i : i + group_size])
        while len(chunk) < group_size:
            try:
                pad = next(pad_iter)
            except StopIteration:
                break
            if pad not in chunk:
                chunk.append(pad)
        groups.append(chunk)
    return groups
```

Зачем padding из existing nm:
- Каждая cabinet group = 1 слот независимо от 1 или 5 nm в ней. Делать неполные группы экономически невыгодно.
- Padding из existing — refresh их данных (free), и в той же группе с новыми nm.

## Шаг 3: Pre-flight (показать стоимость)

```python
plan = {
    "subject_id": 357,
    "subject_name": "Кремы",
    "pool_size": 268,
    "already_bought": 133,
    "new_to_buy": 132,
    "groups": 27,             # 132 / 5 округлено вверх с padding
    "cabinet_slots_cost": 27,
    "period_days": 90,        # cabinet period — typical 90 для long history
}
console.print(f"Plan: {plan['groups']} groups = {plan['cabinet_slots_cost']} slots")
for i, group_nms in enumerate(groups, 1):
    target = [nm for nm in group_nms if nm in new_set]
    pad = [nm for nm in group_nms if nm not in new_set]
    console.print(f"  group {i}: target={target}, +pad: {pad}")
```

Если `--execute` не передан — здесь exit (dry-run). С `--execute` — показать confirmation:

```python
console.print(f"\n[red bold]About to spend {plan['cabinet_slots_cost']} slots. IRREVERSIBLE.[/]")
ans = input("Proceed? type 'yes' to confirm: ").strip().lower()
if ans != "yes":
    console.print("[yellow]Aborted.[/]")
    return
```

⚠ **Это критическая защита.** Никогда не пропускай confirmation. Даже если оператор отвечает `--auto-confirm` — лучше всё равно показать план, ждать `y`.

## Шаг 4: Purchase saga (per группа)

```python
async with WBCabinetClient(cookies) as client:
    for idx, nm_ids in enumerate(groups, 1):
        console.print(f"\n[cyan]group {idx}/{len(groups)}[/] nm_ids={nm_ids}")
        try:
            # 1. Атомарная покупка
            response = await purchase_group(
                client=client,
                nm_ids=nm_ids,
                period_start=period_start_date,
                period_end=period_end_date,
            )
            # → response: cabinet JSON с funnel rows per nm per day

            # 2. Compute comparison_id (deterministic from input)
            cmp_id = compute_comparison_id(
                subject_id=subject_id,
                nm_ids=sorted(nm_ids),
                period_start=period_start_date,
                period_end=period_end_date,
            )
            # cmp_id = "sub357-g123456-p20260219-20260520"

            # 3. ETL: parse response → write to cmp_groups + cmp_funnel_daily
            async with UnitOfWork(engine) as uow:
                await seed_cmp_group_from_payload(
                    uow.session,
                    comparison_id=cmp_id,
                    payload=response,
                    ingest_run_id=ingest_run_id,
                )
                etl = await upsert_funnel_daily_from_comparison(
                    uow.session,
                    comparison_id=cmp_id,
                    comparison_response=response,
                    ingest_run_id=ingest_run_id,
                )
                await uow.commit()

            console.print(f"  [green]ok[/] cmp_id={cmp_id} funnel_rows={etl.rows_written}")
            purchased += 1
        except Exception as exc:
            console.print(f"  [red]X[/] {type(exc).__name__}: {exc}")
            failed += 1
            # Continue with next group — don't strand later groups
```

### `WBCabinetClient` — какие headers/cookies нужны

```python
class WBCabinetClient:
    BASE = "https://seller-content.wildberries.ru"

    def __init__(self, cookies: list[Cookie], timeout: float = 30.0):
        self._client = httpx.AsyncClient(
            base_url=self.BASE,
            cookies={c.name: c.value for c in cookies},
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) ...",
                "Accept": "application/json",
            },
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def post(self, path: str, **kwargs):
        resp = await self._client.post(path, **kwargs)
        if resp.status_code == 401:
            raise CabinetAuthExpired("Cookies expired, re-run setup-cookies")
        resp.raise_for_status()
        return resp.json()
```

### `purchase_group` — реальный endpoint

```python
async def purchase_group(
    client: WBCabinetClient,
    *,
    nm_ids: list[int],
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """
    POST /ns/analytics-api/content-analytics/api/v2/competitor-comparison/nms
    Body: {"nmIDs": [12345, 67890, ...], "periodStart": "2026-02-19", "periodEnd": "2026-05-20"}
    Response: { "data": { "products": [...], "dayDynamics": [...] } }
    """
    body = {
        "nmIDs": sorted(nm_ids),
        "periodStart": period_start.isoformat(),
        "periodEnd": period_end.isoformat(),
    }
    return await client.post(
        "/ns/analytics-api/content-analytics/api/v2/competitor-comparison/nms",
        json=body,
    )
```

## Шаг 5: ETL — payload → cmp_funnel_daily

```python
async def upsert_funnel_daily_from_comparison(
    session: AsyncSession,
    *,
    comparison_id: str,
    comparison_response: dict[str, Any],
    ingest_run_id: int,
) -> EtlResult:
    """
    Parse dayDynamics из response. Each entry: { "nmID": N, "dt": "2026-04-21", "openCardCount": 123, ... }
    Convert revenue rubles → kopeks (×100).
    Convert date string → epoch UTC midnight MSK.
    UPSERT with ON CONFLICT(comparison_id, nm_id, date).
    """
    rows = []
    for entry in comparison_response.get("data", {}).get("dayDynamics", []):
        rows.append({
            "comparison_id": comparison_id,
            "nm_id": int(entry["nmID"]),
            "date": _date_to_epoch_utc_msk_midnight(entry["dt"]),
            "open_card_count": int(entry["openCardCount"]),
            "add_to_cart_count": int(entry["addToCartCount"]),
            "orders_count": int(entry["orderCount"]),
            "orders_sum_kopeks": int(entry["orderSum"] * 100),  # rubles → kopeks
            "ingest_run_id": ingest_run_id,
        })
    stmt = sqlite_insert(CmpFunnelDaily).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=['comparison_id', 'nm_id', 'date'],
        set_={'orders_count': stmt.excluded.orders_count, ...},
    )
    await session.execute(stmt)
    return EtlResult(rows_written=len(rows))
```

Полная реализация — [code/funnel_etl.py](../code/funnel_etl.py).

## Период (period_start / period_end)

**По умолчанию 90 дней.** Это рекомендуется:
- 30 дней → мало history, тренды плохо видно
- 90 дней → 3 месяца, оптимальный баланс
- 365 дней → длинная история но запрос медленнее, larger response

Формула:
```python
today = date.today()
period_end = today - timedelta(days=1)            # MPStats safe (d2 <= today-1)
period_start = period_end - timedelta(days=90)    # 90-day window
```

⚠ **Не меняй period для уже купленных групп!** Период входит в `comparison_id`. Если ты купил группу с period 2026-02-19..2026-05-20, а потом запрашиваешь с period 2026-02-20..2026-05-21 — это **другой cmp_id**, **другой слот**. Будет двойной счёт.

## DLQ (Dead Letter Queue) для failed groups

```python
async def write_failed_to_dlq(
    session: AsyncSession,
    *,
    comparison_id: str,
    nm_ids: list[int],
    error: str,
    ingest_run_id: int,
):
    """
    Записываем в cmp_dlq для retry potом.
    """
    await session.execute(
        insert(CmpDlq).values(
            comparison_id=comparison_id,
            nm_ids_json=json.dumps(nm_ids),
            error_msg=error,
            retry_count=0,
            ingest_run_id=ingest_run_id,
            created_at=int(datetime.now(UTC).timestamp()),
        )
    )
```

Retry CLI:
```bash
wb-pool retry-dlq --max-retries 3
```

Это критично если у тебя ОЧЕНЬ много групп (>100). Для маленьких pools (~30 групп) failure обычно 0% — try/except нужен но DLQ часто не вызывается.

## Output: что в БД после покупки

```sql
-- За одну группу:
SELECT * FROM cmp_groups WHERE comparison_id = 'sub357-g123456-p20260219-20260520';
-- → 1 row: status='ready', nm_ids_json=[1,2,3,4,5], period_start/end

SELECT COUNT(*) FROM cmp_funnel_daily WHERE comparison_id = '...';
-- → 5 nm × 91 day = 455 rows (если period 90d)

-- За весь purchase:
SELECT
  COUNT(DISTINCT comparison_id) AS groups,
  COUNT(*) AS funnel_rows,
  SUM(orders_sum_kopeks)/100/1e6 AS total_revenue_mln_rub
FROM cmp_funnel_daily
WHERE ingest_run_id = :run_id;
-- → groups=27, funnel_rows=12285 (27×455), total_revenue=N M₽
```

## CLI команды

```bash
# 1. Dry-run (показывает план, не покупает)
wb-pool buy \
  --json data/pools/subject-357-1779316531.json \
  --subject-id 357 --subject-name Кремы \
  --period-days 90

# Output:
# Plan: 27 groups = 27 slots
#   group  1: target=[12345, 67890, ...], +pad: []
#   ...

# 2. Execute (тратит слоты)
echo yes | wb-pool buy \
  --json data/pools/subject-357-1779316531.json \
  --subject-id 357 --subject-name Кремы \
  --period-days 90 \
  --execute

# Output:
# group 1/27 nm_ids=[12345, 67890, ...]
#   ok cmp_id=sub357-g12345-p20260219-20260520 funnel_rows=455
# ...
# Done. purchased=27, failed=0, slots_used=27
```

## Время выполнения

| Кол-во групп | Wall-clock | Cabinet slots |
|---|---|---|
| 10 | ~50 сек | 10 |
| 27 | ~2 мин | 27 |
| 100 | ~7 мин | 100 |

Latency per group: ~3-5 сек (POST + response 30-50 KB JSON).

## После покупки

→ [06-excel-ingest.md](06-excel-ingest.md) — скачать Excel (keywords + warehouse) бесплатно
→ [07-validation.md](07-validation.md) — проверить качество купленных карточек
