"""Incremental cabinet purchase for Algorithm H pool diff.

Reads an Algorithm H output JSON (``data/algo_h/subject-N-*.json``),
diffs against nm that already have cabinet data
(``cmp_funnel_daily``), groups the newly-entered nm into chunks of 5
(padding the last short chunk with random nm from the already-bought
set), and purchases each group atomically.

Default mode is dry-run. Use ``--execute`` to actually spend slots.

Cost model:
- One group of 5 nm = 1 cabinet slot, regardless of how many nm are
  "new" vs "padded".
- Padded nm refresh existing cabinet data (cheap, useful).
- Algorithm H discovery cost is 0 (this is the follow-up).

Does NOT:
- Update ``analytic_category_pool`` snapshot (separate concern).
- Download Excel files (keywords, balances) — separate ETL step.
"""

from __future__ import annotations

import asyncio
import json
import random
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from sqlalchemy import text

from wb_pool.clients.cabinet import WBCabinetClient
from wb_pool.clients.cookies import load_cookies
from wb_pool.config import get_settings
from wb_pool.db import UnitOfWork, create_engine
from wb_pool.etl.funnel import (
    seed_cmp_group_from_payload,
    upsert_funnel_daily_from_comparison,
)
from wb_pool.purchase.groups import (
    GroupPlan,
    compute_comparison_id,
    purchase_group,
)

_app = typer.Typer(add_completion=False, no_args_is_help=True)
_console = Console()
_GROUP_SIZE = 5


def _chunked_with_padding(
    targets: list[int],
    padding_pool: list[int],
    *,
    group_size: int = _GROUP_SIZE,
    rng_seed: int = 42,
) -> list[list[int]]:
    """Partition targets into groups of group_size; pad last short group from padding_pool."""
    groups: list[list[int]] = []
    rng = random.Random(rng_seed)
    available_padding = list(padding_pool)
    rng.shuffle(available_padding)
    pad_iter = iter(available_padding)

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


@_app.command()
def buy_algo_h_diff(
    json_path: Path = typer.Option(  # noqa: B008
        ..., "--json", help="Algorithm H output JSON path"
    ),
    subject_id: int = typer.Option(..., "--subject-id"),
    subject_name: str = typer.Option(..., "--subject-name"),
    period_days: int = typer.Option(
        90,
        "--period-days",
        min=1,
        help="Window for cabinet comparison purchase (days back from yesterday).",
    ),
    execute: bool = typer.Option(
        False,
        "--execute",
        help="Actually purchase. Default is dry-run (prints plan + exits).",
    ),
) -> None:
    """Incremental cabinet purchase for Algorithm H pool diff."""
    asyncio.run(
        _main(
            json_path=json_path,
            subject_id=subject_id,
            subject_name=subject_name,
            period_days=period_days,
            execute=execute,
        )
    )


async def _main(
    *,
    json_path: Path,
    subject_id: int,
    subject_name: str,
    period_days: int,
    execute: bool,
) -> None:
    today = date.today()
    yesterday = today - timedelta(days=1)
    period_end_date = yesterday
    period_start_date = yesterday - timedelta(days=period_days)

    _console.print(
        f"[bold]Cabinet purchase for Algorithm H diff -- subject "
        f"{subject_id} ({subject_name})[/bold]"
    )
    _console.print(
        f"[dim]Cabinet period: {period_start_date} -> {period_end_date}[/dim]"
    )
    _console.print(f"[dim]Mode: {'EXECUTE' if execute else 'dry-run'}[/dim]")
    _console.print(f"[dim]Source JSON: {json_path}[/dim]\n")

    if not json_path.exists():
        _console.print(f"[red]X[/red] JSON not found: {json_path}")
        raise typer.Exit(code=1)
    data = json.loads(json_path.read_text())
    algo_h_pool: list[int] = list(data["pool_nm_ids"])
    # sticky_base_size — поле Algorithm H. Для Algorithm I (independent layers)
    # этого поля нет; информационно показываем 0.
    sticky_n: int = int(data.get("sticky_base_size", 0))

    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            # "already bought" = any nm in the pool with cabinet funnel data for
            # this period (cmp_funnel_daily). The previous version filtered via
            # analytic_category_pool, which misses nm bought earlier in the same
            # session but not yet promoted into the production pool table.
            ph = ",".join([f":n{i}" for i in range(len(algo_h_pool))])
            params: dict[str, int] = {
                f"n{i}": nm for i, nm in enumerate(algo_h_pool)
            }
            res = await conn.execute(
                text(
                    f"SELECT DISTINCT nm_id FROM cmp_funnel_daily "
                    f"WHERE nm_id IN ({ph})"
                ),
                params,
            )
            with_cabinet_data: set[int] = {int(row[0]) for row in res}

        pool_set = set(algo_h_pool)
        already_bought = sorted(pool_set & with_cabinet_data)
        new_to_buy = [nm for nm in algo_h_pool if nm not in with_cabinet_data]
        padding_pool = sorted(with_cabinet_data - pool_set)

        _console.print(f"  Algorithm H pool:               {len(algo_h_pool)} nm")
        _console.print(f"  Sticky base (cabinet >=1M):     {sticky_n} nm")
        _console.print(f"  Already in cabinet data:        {len(already_bought)} nm")
        _console.print(f"  New to buy (Algo-H \\ bought):  {len(new_to_buy)} nm")
        _console.print(f"  Padding pool (in DB, not Algo-H): {len(padding_pool)} nm")

        if not new_to_buy:
            _console.print("[green]ok[/green] no new nm to buy. Exit.")
            return

        groups = _chunked_with_padding(new_to_buy, padding_pool=padding_pool)
        target_set = set(new_to_buy)

        _console.print(
            f"\n[bold]Plan: {len(groups)} groups = {len(groups)} slots[/bold] "
            f"({len(new_to_buy)} target + "
            f"{sum(1 for g in groups for nm in g if nm not in target_set)} padding nm)"
        )
        for i, g in enumerate(groups, 1):
            target = [nm for nm in g if nm in target_set]
            pad = [nm for nm in g if nm not in target_set]
            pad_marker = f"  +pad: {pad}" if pad else ""
            _console.print(f"  group {i:2d}: target={target}{pad_marker}")

        if not execute:
            _console.print(
                "\n[dim]--execute not set -- dry-run mode; exiting without purchase.[/dim]"
            )
            return

        _console.print(
            f"\n[bold red]About to spend {len(groups)} cabinet slots. "
            "IRREVERSIBLE.[/bold red]"
        )
        ans = input("Proceed? type 'yes' to confirm: ").strip().lower()
        if ans != "yes":
            _console.print("[yellow]Aborted by operator.[/yellow]")
            return

        async with engine.begin() as conn:
            result = await conn.execute(
                text(
                    "INSERT INTO raw_runs (run_id, started_at, command, status) "
                    "VALUES (:rid, :ts, 'buy-algo-h-diff', 'running') RETURNING id"
                ),
                {
                    "rid": (
                        f"buy-algo-h-subj{subject_id}-"
                        f"{int(datetime.now(UTC).timestamp())}"
                    ),
                    "ts": int(datetime.now(UTC).timestamp()),
                },
            )
            ingest_run_id = int(result.scalar_one())

        cookies = load_cookies()
        purchased = 0
        failed = 0
        async with WBCabinetClient(cookies) as client:
            for idx, nm_ids in enumerate(groups, 1):
                plan = GroupPlan(nm_ids=list(nm_ids))
                _console.print(
                    f"\n[cyan]group {idx}/{len(groups)}[/cyan] nm_ids={nm_ids}"
                )
                try:
                    response = await purchase_group(
                        client=client,
                        plan=plan,
                        period_start=period_start_date,
                        period_end=period_end_date,
                    )
                    cmp_id = compute_comparison_id(
                        subject_id=subject_id,
                        plan=plan,
                        period_start=period_start_date,
                        period_end=period_end_date,
                    )
                    async with UnitOfWork(engine) as uow:
                        await seed_cmp_group_from_payload(
                            uow.session,
                            comparison_id=cmp_id,
                            payload=response,
                            ingest_run_id=ingest_run_id,
                            category_job_id=None,
                        )
                        etl = await upsert_funnel_daily_from_comparison(
                            uow.session,
                            comparison_id=cmp_id,
                            comparison_response=response,
                            ingest_run_id=ingest_run_id,
                        )
                        await uow.commit()
                    _console.print(
                        f"  [green]ok[/green] cmp_id={cmp_id}  "
                        f"funnel_rows={etl.rows_written}"
                    )
                    purchased += 1
                except Exception as exc:
                    _console.print(f"  [red]X[/red] {type(exc).__name__}: {exc}")
                    failed += 1

        _console.print(
            f"\n[bold]Done.[/bold] purchased={purchased}, failed={failed}, "
            f"slots_used={len(groups)}"
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    _app()
