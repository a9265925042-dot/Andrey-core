"""Ad-hoc Excel ingest for already-purchased comparison_ids.

Slots are NOT spent: Excel files are free per already-paid comparison_id.
Wall-clock ~30 sec per cmp (WB generates the XLSX async, we poll).

Looks up cmp_groups by ``--comparison-id`` (one or more) OR by
``--subject-id + --period-start + --period-end``, then for each cmp:

1. Reads nm_ids + period from cmp_groups.
2. Calls ``import_comparison_to_db`` -- WB cabinet exports XLSX, we save
   the ZIP to ``data/cabinet/excel/<wb_uuid>.zip``, parse it, upsert into
   ``cmp_search_queries``, ``cmp_search_query_per_nm``,
   ``cmp_warehouse_metrics``, ``cmp_size_stocks``.
3. Logs per-table row counts.

Re-running is safe -- ``import_comparison_to_db`` is idempotent (ON
CONFLICT upsert + WB returns existing report when re-requested).

Usage:
    # By specific cmp_id list:
    uv run python scripts/ingest_comparison_excel.py \\
        --comparison-id sub357-g27481404-p20260217-20260518 \\
        --comparison-id sub357-g273396353-p20260217-20260518

    # All cmp_groups for a subject + period:
    uv run python scripts/ingest_comparison_excel.py \\
        --subject-id 357 --period-start 2026-02-17 --period-end 2026-05-18
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime

import typer
from rich.console import Console
from sqlalchemy import bindparam, text

from wb_pool.clients.cabinet import WBCabinetClient
from wb_pool.clients.cookies import load_cookies
from wb_pool.config import get_settings
from wb_pool.db import create_engine
from wb_pool.etl.excel import import_comparison_to_db

_app = typer.Typer(add_completion=False, no_args_is_help=True)
_console = Console()


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=UTC).date()


@_app.command()
def ingest(
    comparison_id: list[str] | None = typer.Option(  # noqa: B008
        None, "--comparison-id",
        help="One or more comparison_id. Repeat the flag for multiple.",
    ),
    subject_id: int | None = typer.Option(
        None, "--subject-id",
        help="Alternative: pick all cmp_groups for this subject+period.",
    ),
    period_start: str | None = typer.Option(
        None, "--period-start",
        help="YYYY-MM-DD. Required with --subject-id.",
    ),
    period_end: str | None = typer.Option(
        None, "--period-end",
        help="YYYY-MM-DD. Required with --subject-id.",
    ),
) -> None:
    """Download + parse + upsert Excel for already-purchased comparisons."""
    asyncio.run(_main(
        cmp_ids=list(comparison_id or []),
        subject_id=subject_id,
        period_start=period_start,
        period_end=period_end,
    ))


async def _main(
    *,
    cmp_ids: list[str],
    subject_id: int | None,
    period_start: str | None,
    period_end: str | None,
) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)

    if not cmp_ids and subject_id is None:
        _console.print("[red]Need either --comparison-id or --subject-id.[/red]")
        raise typer.Exit(code=1)

    targets: list[tuple[str, list[int], date, date]] = []
    async with engine.connect() as conn:
        if cmp_ids:
            stmt = text(
                "SELECT comparison_id, nm_ids_json, period_start, period_end "
                "FROM cmp_groups WHERE comparison_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True))
            r = await conn.execute(stmt, {"ids": list(cmp_ids)})
        else:
            assert subject_id is not None and period_start and period_end
            ps_epoch = int(datetime.combine(_parse_date(period_start), datetime.min.time(), tzinfo=UTC).timestamp())
            pe_epoch = int(datetime.combine(_parse_date(period_end), datetime.min.time(), tzinfo=UTC).timestamp())
            r = await conn.execute(
                text(
                    "SELECT comparison_id, nm_ids_json, period_start, period_end "
                    "FROM cmp_groups "
                    "WHERE comparison_id LIKE :prefix "
                    "  AND period_start = :ps AND period_end = :pe"
                ),
                {"prefix": f"sub{subject_id}-%", "ps": ps_epoch, "pe": pe_epoch},
            )
        for row in r:
            cmp_id, nm_json, ps, pe = row
            nm_ids = json.loads(nm_json) if isinstance(nm_json, str) else list(nm_json)
            ps_date = datetime.fromtimestamp(ps, tz=UTC).date()
            pe_date = datetime.fromtimestamp(pe, tz=UTC).date()
            targets.append((cmp_id, nm_ids, ps_date, pe_date))

    if not targets:
        _console.print("[red]No cmp_groups matched. Nothing to do.[/red]")
        return

    _console.print(f"[bold]Found {len(targets)} cmp_groups to ingest Excel for:[/bold]")
    for cmp_id, nm_ids, ps, pe in targets:
        _console.print(f"  {cmp_id}  nm={nm_ids}  period={ps}..{pe}")

    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO raw_runs (run_id, started_at, command, status) "
                "VALUES (:rid, :ts, 'ingest-cmp-excel', 'running') RETURNING id"
            ),
            {
                "rid": f"ingest-excel-{int(datetime.now(UTC).timestamp())}",
                "ts": int(datetime.now(UTC).timestamp()),
            },
        )
        ingest_run_id = int(result.scalar_one())

    cookies = load_cookies()
    succeeded = 0
    failed = 0
    try:
        async with WBCabinetClient(cookies) as client:
            for cmp_id, nm_ids, ps, pe in targets:
                _console.print(f"\n[cyan]{cmp_id}[/cyan]  nm={nm_ids}")
                try:
                    zip_path, _entry, stats = await import_comparison_to_db(
                        client,
                        nm_ids=nm_ids,
                        period_start=ps,
                        period_end=pe,
                        engine=engine,
                        ingest_run_id=ingest_run_id,
                        comparison_id=cmp_id,
                    )
                    rows_total = sum(stats.values())
                    _console.print(
                        f"  [green]ok[/green] zip={zip_path.name}  upserts={stats}  total={rows_total}"
                    )
                    succeeded += 1
                except Exception as exc:
                    _console.print(f"  [red]X[/red] {type(exc).__name__}: {exc}")
                    failed += 1
    finally:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE raw_runs SET status='done' WHERE id=:i"),
                {"i": ingest_run_id},
            )
        await engine.dispose()

    _console.print(
        f"\n[bold]Done.[/bold] succeeded={succeeded} failed={failed} ingest_run_id={ingest_run_id}"
    )


if __name__ == "__main__":
    _app()
