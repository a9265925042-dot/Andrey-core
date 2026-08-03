"""wb-pool CLI — собирается из cli_*.py + собственных команд.

Команды (см. SKILL.md → «CLI шпаргалка»):
  setup-cookies / doctor / migrate
  pull-cards / pull-mpstats-keywords
  discover / buy / ingest-excel / retry-dlq / report
  run-category (оркестратор, 8 фаз)
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import typer
from rich.console import Console
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool import cli_buy, cli_discover, cli_ingest_excel
from wb_pool.config import get_settings
from wb_pool.db import UnitOfWork, apply_schema, create_engine

app = typer.Typer(add_completion=False, no_args_is_help=True)
_console = Console()

# Provided command modules mounted as subcommands
app.command("discover")(cli_discover.main)
app.command("buy")(cli_buy.buy_algo_h_diff)
app.command("ingest-excel")(cli_ingest_excel.ingest)


async def _new_run(engine: AsyncEngine, command: str) -> int:
    now = int(datetime.now(UTC).timestamp())
    async with engine.begin() as conn:
        result = await conn.execute(
            text(
                "INSERT INTO raw_runs (run_id, started_at, command, status) "
                "VALUES (:rid, :ts, :cmd, 'running') RETURNING id"
            ),
            {"rid": f"{command}-{now}", "ts": now, "cmd": command},
        )
        return int(result.scalar_one())


async def _finish_run(engine: AsyncEngine, run_id: int, status: str = "done") -> None:
    async with engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE raw_runs SET status=:st, finished_at=:ts WHERE id=:i"
            ),
            {"st": status, "ts": int(datetime.now(UTC).timestamp()), "i": run_id},
        )


# ---------------------------------------------------------------------------
# setup-cookies
# ---------------------------------------------------------------------------


@app.command("setup-cookies")
def setup_cookies(
    cookie_file: Path | None = typer.Option(  # noqa: B008
        None,
        "--cookie-file",
        help="Явный путь к Chrome Cookies файлу (не-Default профиль).",
    ),
) -> None:
    """Импорт *.wildberries.ru cookies из Chrome (Chrome должен быть закрыт)."""
    from wb_pool.clients.cookies import import_chrome_cookies, load_cookies, oldest_expiry

    settings = get_settings()
    effective = cookie_file or (
        Path(settings.chrome_cookie_file) if settings.chrome_cookie_file else None
    )
    output = Path(settings.wb_cabinet_cookies_path)
    try:
        count = import_chrome_cookies(output_path=output, cookie_file=effective)
    except Exception as exc:
        _console.print(f"[red]X[/red] cookie import failed: {type(exc).__name__}: {exc}")
        _console.print(
            "Проверь: Chrome закрыт? Профиль Default? macOS Full Disk Access? "
            "См. docs/skill/docs/01-onboarding.md"
        )
        raise typer.Exit(code=1) from exc
    expiry = oldest_expiry(load_cookies(output))
    expiry_str = expiry.date().isoformat() if expiry else "session-only"
    _console.print(
        f"[green]OK[/green]: imported {count} cabinet cookies, oldest expires {expiry_str}"
    )


# ---------------------------------------------------------------------------
# migrate
# ---------------------------------------------------------------------------


@app.command("migrate")
def migrate() -> None:
    """Создать/обновить схему БД (tables + views + WAL). Idempotent."""

    async def _run() -> None:
        settings = get_settings()
        Path("data").mkdir(exist_ok=True)
        engine = create_engine(settings.database_url)
        try:
            await apply_schema(engine)
        finally:
            await engine.dispose()

    asyncio.run(_run())
    _console.print("[green]OK[/green]: schema applied")


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


@app.command("doctor")
def doctor(
    offline: bool = typer.Option(
        False, "--offline", help="Пропустить live canary запросы."
    ),
) -> None:
    """Health-check: config, DB, cookies + live canaries всех каналов."""
    asyncio.run(_doctor(offline=offline))


async def _doctor(*, offline: bool) -> None:
    settings = get_settings()
    ok = "[green]OK[/green]"
    fail = "[red]FAIL[/red]"
    warn = "[yellow]WARN[/yellow]"

    _console.print("[bold]Config:[/bold]")
    _console.print(
        f"  {'WB_API_TOKEN set' if settings.wb_api_token else 'WB_API_TOKEN missing'}: "
        f"{ok if settings.wb_api_token else fail}"
    )
    _console.print(
        f"  {'MPSTATS_API_TOKEN set' if settings.mpstats_api_token else 'MPSTATS_API_TOKEN missing'}: "
        f"{ok if settings.mpstats_api_token else fail}"
    )
    _console.print(f"  MPSTATS_PARENT_CATEGORY: '{settings.mpstats_parent_category}'")
    markers = sorted(settings.brand_markers_set)
    _console.print(
        f"  MY_BRAND_MARKERS: {markers if markers else fail + ' (пусто — narrow SERP будет тянуть брендовые ключи)'}"
    )

    _console.print("\n[bold]Database:[/bold]")
    engine = create_engine(settings.database_url)
    try:
        async with engine.connect() as conn:
            res = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
            )
            tables = [row[0] for row in res]
        expected = {
            "raw_runs", "wb_cards", "mpstats_keywords", "cmp_groups",
            "cmp_funnel_daily", "cmp_search_queries", "cmp_search_query_per_nm",
            "cmp_warehouse_metrics", "cmp_size_stocks", "cmp_dlq",
        }
        missing = expected - set(tables)
        if missing:
            _console.print(f"  Schema: {fail} missing tables: {sorted(missing)} — run `wb-pool migrate`")
        else:
            _console.print(f"  Schema: {ok} ({len(tables)} tables)")
            async with engine.connect() as conn:
                for label, sql in [
                    ("wb_cards rows", "SELECT COUNT(*) FROM wb_cards"),
                    ("mpstats_keywords rows", "SELECT COUNT(*) FROM mpstats_keywords"),
                    ("cmp_groups", "SELECT COUNT(*) FROM cmp_groups"),
                ]:
                    n = (await conn.execute(text(sql))).scalar_one()
                    _console.print(f"  {label}: {n}")
    except Exception as exc:
        _console.print(f"  DB: {fail} {type(exc).__name__}: {exc}")
    finally:
        await engine.dispose()

    _console.print("\n[bold]Cabinet cookies:[/bold]")
    cookies = None
    try:
        from wb_pool.clients.cookies import load_cookies, oldest_expiry

        cookies = load_cookies()
        expiry = oldest_expiry(cookies)
        _console.print(
            f"  Imported: {ok} {len(cookies)} cookies, oldest expires "
            f"{expiry.date().isoformat() if expiry else 'session-only'}"
        )
    except FileNotFoundError:
        _console.print(f"  Imported: {warn} not found — run `wb-pool setup-cookies`")

    if offline:
        _console.print("\n[dim]--offline: live canaries skipped[/dim]")
        return

    _console.print("\n[bold]Live canaries:[/bold]")
    yesterday = date.today() - timedelta(days=1)

    if settings.wb_api_token:
        token = settings.wb_api_token.get_secret_value()
        try:
            from wb_pool.clients.wb_seller import WBAPIClient

            async with WBAPIClient.content(token=token, timeout=15.0) as client:
                await client.post(
                    "/content/v2/get/cards/list",
                    json={"settings": {"cursor": {"limit": 1}, "filter": {"withPhoto": -1}}},
                )
            _console.print(f"  WB Seller API (content): {ok}")
        except Exception as exc:
            _console.print(f"  WB Seller API (content): {fail} {type(exc).__name__}: {exc}")
        try:
            from wb_pool.clients.wb_seller import WBAPIClient

            async with WBAPIClient.analytics(token=token, timeout=15.0) as client:
                await client.post(
                    "/api/analytics/v3/sales-funnel/products",
                    json={
                        "period": {
                            "start": (yesterday - timedelta(days=30)).isoformat(),
                            "end": yesterday.isoformat(),
                        },
                        "limit": 1,
                    },
                )
            _console.print(f"  WB Seller Jam (sales-funnel): {ok}")
        except Exception as exc:
            _console.print(
                f"  WB Seller Jam (sales-funnel): {warn} {type(exc).__name__} "
                f"— используй --manual-top-nm-ids в discover"
            )
    if settings.mpstats_api_token:
        try:
            from wb_pool.discovery.mpstats_layer import fetch_mpstats_top_n_in_window

            await fetch_mpstats_top_n_in_window(
                token=settings.mpstats_api_token.get_secret_value(),
                subject_id=357,
                parent_category=settings.mpstats_parent_category,
                period_start=yesterday - timedelta(days=30),
                period_end=yesterday,
                top_n=1,
            )
            _console.print(f"  MPStats API: {ok} (1 op spent)")
        except Exception as exc:
            _console.print(f"  MPStats API: {fail} {type(exc).__name__}: {exc}")
    if cookies is not None:
        try:
            from wb_pool.clients.cabinet import WBCabinetClient

            async with WBCabinetClient(cookies, timeout=15.0) as cab:
                limits = await cab.limits()
            _console.print(f"  Cabinet limits: {ok} {limits}")
        except Exception as exc:
            _console.print(
                f"  Cabinet limits: {fail} {type(exc).__name__}: {exc} "
                f"— re-run `wb-pool setup-cookies`"
            )


# ---------------------------------------------------------------------------
# pull-cards / pull-mpstats-keywords
# ---------------------------------------------------------------------------


@app.command("pull-cards")
def pull_cards() -> None:
    """Загрузить мои карточки (Seller API content list) → wb_cards."""

    async def _run() -> None:
        settings = get_settings()
        if settings.wb_api_token is None:
            _console.print("[red]WB_API_TOKEN не задан в .env[/red]")
            raise typer.Exit(code=1)
        engine = create_engine(settings.database_url)
        try:
            run_id = await _new_run(engine, "pull-cards")
            from wb_pool.etl.pull_cards import pull_my_cards

            total = await pull_my_cards(
                engine,
                token=settings.wb_api_token.get_secret_value(),
                ingest_run_id=run_id,
            )
            await _finish_run(engine, run_id)
            _console.print(f"[green]OK[/green]: wb_cards upserted {total} rows")
        finally:
            await engine.dispose()

    asyncio.run(_run())


@app.command("pull-mpstats-keywords")
def pull_mpstats_keywords_cmd(
    subject_id: int = typer.Option(..., "--subject-id"),
    max_cards: int = typer.Option(20, "--max-cards", min=1),
    debug_dump: bool = typer.Option(
        False, "--debug-dump", help="Сохранить raw JSON ответы в data/logs/mpstats/"
    ),
) -> None:
    """Загрузить MPStats keywords для моих карточек subject'а → mpstats_keywords."""

    async def _run() -> None:
        settings = get_settings()
        if settings.mpstats_api_token is None:
            _console.print("[red]MPSTATS_API_TOKEN не задан в .env[/red]")
            raise typer.Exit(code=1)
        engine = create_engine(settings.database_url)
        try:
            run_id = await _new_run(engine, "pull-mpstats-keywords")
            from wb_pool.etl.mpstats_keywords import pull_mpstats_keywords

            total = await pull_mpstats_keywords(
                engine,
                token=settings.mpstats_api_token.get_secret_value(),
                subject_id=subject_id,
                ingest_run_id=run_id,
                max_cards=max_cards,
                debug_dump_dir=Path("data/logs/mpstats") if debug_dump else None,
            )
            await _finish_run(engine, run_id)
            _console.print(f"[green]OK[/green]: mpstats_keywords upserted {total} rows")
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# retry-dlq
# ---------------------------------------------------------------------------


@app.command("retry-dlq")
def retry_dlq(
    max_retries: int = typer.Option(3, "--max-retries", min=1),
    period_days: int = typer.Option(90, "--period-days", min=1),
) -> None:
    """Повторить failed purchases из cmp_dlq (unresolved, retry_count < max)."""

    async def _run() -> None:
        from wb_pool.clients.cabinet import WBCabinetClient
        from wb_pool.clients.cookies import load_cookies
        from wb_pool.etl.funnel import (
            seed_cmp_group_from_payload,
            upsert_funnel_daily_from_comparison,
        )
        from wb_pool.purchase.groups import GroupPlan, purchase_group

        settings = get_settings()
        engine = create_engine(settings.database_url)
        try:
            async with engine.connect() as conn:
                res = await conn.execute(
                    text(
                        "SELECT id, comparison_id, nm_ids_json, retry_count FROM cmp_dlq "
                        "WHERE resolved_at IS NULL AND retry_count < :mx"
                    ),
                    {"mx": max_retries},
                )
                entries = [(int(r[0]), str(r[1]), json.loads(r[2]), int(r[3])) for r in res]
            if not entries:
                _console.print("[green]OK[/green]: DLQ пуст")
                return
            run_id = await _new_run(engine, "retry-dlq")
            yesterday = date.today() - timedelta(days=1)
            ps, pe = yesterday - timedelta(days=period_days), yesterday
            cookies = load_cookies()
            resolved = 0
            async with WBCabinetClient(cookies) as client:
                for dlq_id, cmp_id, nm_ids, retry_count in entries:
                    try:
                        response = await purchase_group(
                            client=client,
                            plan=GroupPlan(nm_ids=nm_ids),
                            period_start=ps,
                            period_end=pe,
                        )
                        async with UnitOfWork(engine) as uow:
                            await seed_cmp_group_from_payload(
                                uow.session,
                                comparison_id=cmp_id,
                                payload=response,
                                ingest_run_id=run_id,
                            )
                            await upsert_funnel_daily_from_comparison(
                                uow.session,
                                comparison_id=cmp_id,
                                comparison_response=response,
                                ingest_run_id=run_id,
                            )
                            await uow.session.execute(
                                text("UPDATE cmp_dlq SET resolved_at=:ts WHERE id=:i"),
                                {"ts": int(datetime.now(UTC).timestamp()), "i": dlq_id},
                            )
                            await uow.commit()
                        resolved += 1
                        _console.print(f"  [green]ok[/green] {cmp_id}")
                    except Exception as exc:
                        async with engine.begin() as conn:
                            await conn.execute(
                                text(
                                    "UPDATE cmp_dlq SET retry_count=:rc, error_msg=:em "
                                    "WHERE id=:i"
                                ),
                                {
                                    "rc": retry_count + 1,
                                    "em": f"{type(exc).__name__}: {exc}"[:2000],
                                    "i": dlq_id,
                                },
                            )
                        _console.print(f"  [red]X[/red] {cmp_id}: {type(exc).__name__}: {exc}")
            await _finish_run(engine, run_id)
            _console.print(
                f"[bold]Done.[/bold] resolved={resolved}/{len(entries)}"
            )
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


@app.command("report")
def report_cmd(
    subject_id: int = typer.Option(..., "--subject-id"),
    subject_name: str = typer.Option("", "--subject-name"),
    pool_json: Path | None = typer.Option(None, "--pool-json"),  # noqa: B008
) -> None:
    """Validation report: tier distribution, per-layer quality, top-new."""

    async def _run() -> None:
        from wb_pool.report import build_report, render_report

        settings = get_settings()
        engine = create_engine(settings.database_url)
        try:
            report = await build_report(
                engine, subject_id=subject_id, pool_json_path=pool_json
            )
            _console.print(render_report(report, subject_name=subject_name))
        finally:
            await engine.dispose()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# run-category (оркестратор — 8 фаз, см. docs/08-orchestrator.md)
# ---------------------------------------------------------------------------


@app.command("run-category")
def run_category(
    subject_id: int = typer.Option(..., "--subject-id"),
    subject_name: str = typer.Option(..., "--subject-name"),
    serp_top: int = typer.Option(100, "--serp-top", min=1),
    feedbacks_threshold: int = typer.Option(300, "--feedbacks-threshold", min=0),
    n_my_cards: int = typer.Option(20, "--n-my-cards", min=1),
    period_days: int = typer.Option(90, "--period-days", min=1),
    mpstats_parent_category: str | None = typer.Option(None, "--mpstats-parent-category"),
    pull_cards_first: bool = typer.Option(True, "--pull-cards/--no-pull-cards"),
    pull_mpstats: bool = typer.Option(True, "--pull-mpstats/--no-pull-mpstats"),
    discover_only: bool = typer.Option(False, "--discover-only"),
    auto_confirm: bool = typer.Option(
        False, "--auto-confirm", help="ОПАСНО: пропустить подтверждение перед тратой слотов."
    ),
) -> None:
    """Full pipeline: preflight → pulls → discovery → diff → buy → excel → report."""
    asyncio.run(
        _run_category(
            subject_id=subject_id,
            subject_name=subject_name,
            serp_top=serp_top,
            feedbacks_threshold=feedbacks_threshold,
            n_my_cards=n_my_cards,
            period_days=period_days,
            mpstats_parent_category=mpstats_parent_category,
            pull_cards_first=pull_cards_first,
            pull_mpstats=pull_mpstats,
            discover_only=discover_only,
            auto_confirm=auto_confirm,
        )
    )


async def _run_category(
    *,
    subject_id: int,
    subject_name: str,
    serp_top: int,
    feedbacks_threshold: int,
    n_my_cards: int,
    period_days: int,
    mpstats_parent_category: str | None,
    pull_cards_first: bool,
    pull_mpstats: bool,
    discover_only: bool,
    auto_confirm: bool,
) -> None:
    from wb_pool.discovery.algo_i import run_algo_i
    from wb_pool.discovery.algo_i_types import AlgorithmIConfig
    from wb_pool.logging_setup import get_logger
    from wb_pool.purchase.groups import chunk_with_padding, diff_with_purchased

    log = get_logger(__name__).bind(subject_id=subject_id)
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        # --- Phase 1: Preflight ---
        errors: list[str] = []
        if settings.wb_api_token is None:
            errors.append("WB_API_TOKEN missing")
        if settings.mpstats_api_token is None:
            errors.append("MPSTATS_API_TOKEN missing (Layer C будет пропущен)")
        cookies = None
        if not discover_only:
            try:
                from wb_pool.clients.cookies import load_cookies

                cookies = load_cookies()
            except FileNotFoundError:
                errors.append("cabinet cookies not imported (wb-pool setup-cookies)")
        async with engine.connect() as conn:
            res = await conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table' AND name='cmp_funnel_daily'")
            )
            if res.first() is None:
                errors.append("schema missing (wb-pool migrate)")
        hard_errors = [e for e in errors if "Layer C" not in e]
        for e in errors:
            _console.print(f"  [yellow]![/yellow] {e}")
        if hard_errors:
            log.error("preflight_failed", details=hard_errors)
            raise typer.Exit(code=1)
        log.info("preflight_ok")

        # --- Phase 2: Pulls (idempotent) ---
        if pull_cards_first and settings.wb_api_token is not None:
            run_id = await _new_run(engine, "pull-cards")
            from wb_pool.etl.pull_cards import pull_my_cards

            await pull_my_cards(
                engine,
                token=settings.wb_api_token.get_secret_value(),
                ingest_run_id=run_id,
            )
            await _finish_run(engine, run_id)
        if pull_mpstats and settings.mpstats_api_token is not None:
            run_id = await _new_run(engine, "pull-mpstats-keywords")
            from wb_pool.etl.mpstats_keywords import pull_mpstats_keywords

            await pull_mpstats_keywords(
                engine,
                token=settings.mpstats_api_token.get_secret_value(),
                subject_id=subject_id,
                ingest_run_id=run_id,
                max_cards=n_my_cards,
            )
            await _finish_run(engine, run_id)

        # --- Phase 3: Discovery ---
        config = AlgorithmIConfig(
            subject_id=subject_id,
            subject_name=subject_name,
            serp_top=serp_top,
            serp_narrow_top=max(1, serp_top // 5),
            mpstats_per_window=max(1, serp_top // 4),
            n_my_cards=n_my_cards,
            feedbacks_threshold=feedbacks_threshold,
            mpstats_parent_category=(
                mpstats_parent_category or settings.mpstats_parent_category
            ),
        )
        log.info("discovery_start")
        pool_result = await run_algo_i(engine=engine, config=config)
        log.info(
            "discovery_done",
            pool_size=len(pool_result.pool_nm_ids),
            layer_a=pool_result.layer_a_serp_main_count,
            layer_b=pool_result.layer_b_serp_narrow_count,
            layer_c=pool_result.layer_c_mpstats_count,
        )
        out_dir = Path("data/pools")
        out_dir.mkdir(parents=True, exist_ok=True)
        pool_json_path = out_dir / f"subject-{subject_id}-{int(time.time())}.json"
        result_dict = asdict(pool_result)
        result_dict["layer_membership"] = {
            str(k): v for k, v in pool_result.layer_membership.items()
        }
        pool_json_path.write_text(json.dumps(result_dict, ensure_ascii=False, indent=2))
        _console.print(
            f"Pool: {len(pool_result.pool_nm_ids)} nm "
            f"(A={pool_result.layer_a_serp_main_count}, "
            f"B={pool_result.layer_b_serp_narrow_count}, "
            f"C={pool_result.layer_c_mpstats_count}) → {pool_json_path}"
        )
        if discover_only:
            _console.print("[dim]--discover-only: остановка перед покупкой.[/dim]")
            return

        # --- Phase 4: Diff ---
        new_nm, already_bought = await diff_with_purchased(
            pool_result.pool_nm_ids, engine=engine
        )
        groups = chunk_with_padding(new_nm, padding_pool=already_bought)
        log.info(
            "purchase_plan",
            groups=len(groups),
            new_nm=len(new_nm),
            already_bought=len(already_bought),
        )
        if not groups:
            _console.print("[green]ok[/green] нет новых nm — покупка не нужна.")
            return

        # --- Phase 5: STOP → confirm ---
        target_set = set(new_nm)
        _console.print(
            f"\n[bold]Plan: {len(groups)} groups = {len(groups)} slots[/bold]"
        )
        for i, g in enumerate(groups, 1):
            target = [nm for nm in g if nm in target_set]
            pad = [nm for nm in g if nm not in target_set]
            pad_marker = f"  +pad: {pad}" if pad else ""
            _console.print(f"  group {i:2d}: target={target}{pad_marker}")
        if not auto_confirm:
            _console.print(
                f"\n[bold red]About to spend {len(groups)} cabinet slots. IRREVERSIBLE.[/bold red]"
            )
            ans = input("Proceed? type 'yes' to confirm: ").strip().lower()
            if ans != "yes":
                _console.print("[yellow]Aborted by operator.[/yellow]")
                return

        # --- Phase 6: Purchase saga ---
        from wb_pool.clients.cabinet import WBCabinetClient
        from wb_pool.etl.funnel import (
            seed_cmp_group_from_payload,
            upsert_funnel_daily_from_comparison,
            write_failed_to_dlq,
        )
        from wb_pool.purchase.groups import (
            GroupPlan,
            compute_comparison_id,
            purchase_group,
        )

        yesterday = date.today() - timedelta(days=1)
        ps, pe = yesterday - timedelta(days=period_days), yesterday
        run_id = await _new_run(engine, "run-category")
        purchased = failed = 0
        assert cookies is not None
        async with WBCabinetClient(cookies) as client:
            for idx, nm_ids in enumerate(groups, 1):
                plan = GroupPlan(nm_ids=list(nm_ids))
                cmp_id = compute_comparison_id(
                    subject_id=subject_id, plan=plan, period_start=ps, period_end=pe
                )
                _console.print(f"[cyan]group {idx}/{len(groups)}[/cyan] nm_ids={nm_ids}")
                try:
                    response = await purchase_group(
                        client=client, plan=plan, period_start=ps, period_end=pe
                    )
                    async with UnitOfWork(engine) as uow:
                        await seed_cmp_group_from_payload(
                            uow.session,
                            comparison_id=cmp_id,
                            payload=response,
                            ingest_run_id=run_id,
                        )
                        etl = await upsert_funnel_daily_from_comparison(
                            uow.session,
                            comparison_id=cmp_id,
                            comparison_response=response,
                            ingest_run_id=run_id,
                        )
                        await uow.commit()
                    log.info("purchase_group_ok", cmp_id=cmp_id, rows=etl.rows_written)
                    purchased += 1
                except Exception as exc:
                    log.warning("purchase_group_failed", cmp_id=cmp_id, error=repr(exc))
                    async with UnitOfWork(engine) as uow:
                        await write_failed_to_dlq(
                            uow.session,
                            comparison_id=cmp_id,
                            nm_ids=list(nm_ids),
                            error=f"{type(exc).__name__}: {exc}",
                            ingest_run_id=run_id,
                        )
                        await uow.commit()
                    failed += 1
        log.info("purchase_done", purchased=purchased, failed=failed)

        # --- Phase 7: Excel ingest ---
        from wb_pool.etl.excel import import_comparison_to_db

        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT comparison_id, nm_ids_json FROM cmp_groups "
                    "WHERE comparison_id LIKE :prefix AND ingest_run_id = :rid"
                ),
                {"prefix": f"sub{subject_id}-%", "rid": run_id},
            )
            bought_groups = [(str(r[0]), json.loads(r[1])) for r in res]
        excel_ok = 0
        async with WBCabinetClient(cookies) as client:
            for cmp_id, nm_ids in bought_groups:
                try:
                    await import_comparison_to_db(
                        client,
                        nm_ids=nm_ids,
                        period_start=ps,
                        period_end=pe,
                        engine=engine,
                        ingest_run_id=run_id,
                        comparison_id=cmp_id,
                    )
                    excel_ok += 1
                except Exception as exc:
                    log.warning("excel_ingest_failed", cmp_id=cmp_id, error=repr(exc))
        log.info("excel_ingest_done", cmp_ingested=excel_ok)
        await _finish_run(engine, run_id)

        # --- Phase 8: Validation report ---
        from wb_pool.report import build_report, render_report

        report = await build_report(
            engine,
            subject_id=subject_id,
            pool_json_path=pool_json_path,
            known_before_nm_ids=set(already_bought),
        )
        _console.print("\n" + render_report(report, subject_name=subject_name))
        _console.print(
            f"\n[bold]Done.[/bold] purchased={purchased} failed={failed} "
            f"excel={excel_ok}/{len(bought_groups)}"
        )
        if failed:
            _console.print("Failed groups в DLQ — `wb-pool retry-dlq`")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    app()
