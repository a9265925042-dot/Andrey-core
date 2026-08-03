"""CLI wrapper for Algorithm I — independent layers pool builder.

Usage examples:

  # Large category (Кремы):
  uv run python scripts/run_algo_i.py \\
    --subject-id 357 --subject-name Кремы

  # Small/niche category — tighter knobs:
  uv run python scripts/run_algo_i.py \\
    --subject-id 999 --subject-name "Узкая ниша" \\
    --serp-top 30 --serp-narrow-top 30 --mpstats-per-window 10 \\
    --feedbacks-threshold 100

  # Pre-flight (estimate cost):
  uv run python scripts/run_algo_i.py --subject-id 357 ... --pre-flight

  # No my carto — manual nm seed:
  uv run python scripts/run_algo_i.py --subject-id 357 ... \\
    --manual-top-nm-ids 12345,67890

Output: data/algo_i/subject-<id>-<ts>.json
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from pathlib import Path

import typer
from rich.console import Console

from wb_pool.discovery.algo_i import run_algo_i
from wb_pool.discovery.algo_i_types import AlgorithmIConfig
from wb_pool.config import get_settings
from wb_pool.db import create_engine

_app = typer.Typer(add_completion=False, no_args_is_help=True)
_console = Console()
_OUTPUT_DIR = Path("data/algo_i")


@_app.command()
def main(
    subject_id: int = typer.Option(..., "--subject-id"),
    subject_name: str = typer.Option(..., "--subject-name"),
    serp_top: int = typer.Option(100, "--serp-top", min=1),
    serp_narrow_top: int | None = typer.Option(
        None,
        "--serp-narrow-top",
        help="Top-N per narrow keyword. Default: serp_top // 5 (e.g. 20 for serp_top=100).",
    ),
    mpstats_per_window: int | None = typer.Option(
        None,
        "--mpstats-per-window",
        help="Top-N per MPStats window. Default: serp_top // 4 (e.g. 25 for serp_top=100).",
    ),
    n_my_cards: int = typer.Option(20, "--n-my-cards", min=1),
    feedbacks_threshold: int = typer.Option(300, "--feedbacks-threshold", min=0),
    mpstats_parent_category: str = typer.Option(
        "Красота", "--mpstats-parent-category"
    ),
    period_days: int = typer.Option(30, "--period-days", min=1),
    manual_top_nm_ids: str | None = typer.Option(
        None, "--manual-top-nm-ids", help="Comma-separated nm_ids"
    ),
    pre_flight: bool = typer.Option(False, "--pre-flight"),
) -> None:
    """Run Algorithm I on one subject; write JSON output."""
    manual_list = (
        [int(x) for x in manual_top_nm_ids.split(",") if x.strip()]
        if manual_top_nm_ids
        else None
    )
    # Auto-derive narrow top from main top if not set: узкие ниши не нуждаются в
    # такой же глубине как category-wide top.
    effective_narrow_top = (
        serp_narrow_top if serp_narrow_top is not None else max(1, serp_top // 5)
    )
    # MPStats — cleanest source, can afford more depth → serp_top // 4.
    effective_mpstats_per_window = (
        mpstats_per_window if mpstats_per_window is not None else max(1, serp_top // 4)
    )
    config = AlgorithmIConfig(
        subject_id=subject_id,
        subject_name=subject_name,
        serp_top=serp_top,
        serp_narrow_top=effective_narrow_top,
        mpstats_per_window=effective_mpstats_per_window,
        n_my_cards=n_my_cards,
        feedbacks_threshold=feedbacks_threshold,
        mpstats_parent_category=mpstats_parent_category,
        period_days=period_days,
        manual_top_nm_ids=manual_list,
    )
    if pre_flight:
        _print_pre_flight(config)
        raise typer.Exit(code=0)
    asyncio.run(_run(config))


def _print_pre_flight(cfg: AlgorithmIConfig) -> None:
    main_pages = (cfg.serp_top + 99) // 100
    narrow_pages_total = ((cfg.serp_narrow_top + 99) // 100) * cfg.n_my_cards
    has_manual = cfg.manual_top_nm_ids is not None
    sales_funnel_calls = 0 if has_manual else 1
    _console.print(
        f"[bold]Pre-flight Algorithm I -- subject {cfg.subject_id} "
        f"({cfg.subject_name})[/bold]"
    )
    _console.print(f"  feedbacks_threshold: {cfg.feedbacks_threshold}")
    _console.print(f"  period: last {cfg.period_days} days")
    _console.print(f"  output: data/algo_i/subject-{cfg.subject_id}-<ts>.json")
    _console.print("\n  Layer A -- SERP main:")
    _console.print(
        f"    {sales_funnel_calls} sales-funnel call "
        f"(top-{cfg.n_my_cards} my carto for narrow kw seed) + "
        f"{main_pages} SERP pages x 100 = {main_pages * 100} rows  "
        f"[query='{cfg.subject_name}']"
    )
    _console.print("\n  Layer B -- SERP narrow:")
    _console.print(
        f"    up to {cfg.n_my_cards} narrow keywords x "
        f"{(cfg.serp_narrow_top + 99) // 100} pages x 100 = "
        f"up to {narrow_pages_total * 100} rows (Semaphore(5))"
    )
    _console.print("\n  Layer C -- MPStats 3 windows:")
    _console.print(
        f"    3 ops (live/june/year), top-{cfg.mpstats_per_window} each "
        f"(of 150/мес budget)"
    )
    _console.print("\n  CardDetail batches: 1-5 batch HTTP")
    _console.print("  Cabinet impact: [green]0 slots[/green] (discovery-only)")


async def _run(config: AlgorithmIConfig) -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        _console.print(f"[cyan]Running Algorithm I on subject {config.subject_id}...[/cyan]")
        result = await run_algo_i(engine=engine, config=config)
    finally:
        await engine.dispose()

    _OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = int(time.time())
    out_path = _OUTPUT_DIR / f"subject-{config.subject_id}-{ts}.json"

    # layer_membership: JSON keys must be strings
    result_dict = asdict(result)
    result_dict["layer_membership"] = {
        str(k): v for k, v in result.layer_membership.items()
    }
    out_path.write_text(json.dumps(result_dict, ensure_ascii=False, indent=2))

    _console.print(f"\n[green]Done.[/green] pool_size={len(result.pool_nm_ids)}")
    _console.print(f"  Layer A SERP main: {result.layer_a_serp_main_count}")
    _console.print(f"  Layer B SERP narrow: {result.layer_b_serp_narrow_count}")
    _console.print(f"  Layer C MPStats: {result.layer_c_mpstats_count}")
    _console.print(f"  Dropped low feedbacks: {result.dropped_low_feedbacks_per_layer}")
    _console.print(f"  Dropped deleted on WB: {result.dropped_deleted_on_wb}")
    _console.print(f"  Narrow kws ({len(result.narrow_kws_used)}): {result.narrow_kws_used}")
    _console.print(f"  Duration: {result.duration_seconds:.1f}s")
    _console.print(f"  Output: {out_path}")


if __name__ == "__main__":
    _app()
