"""Dashboard generator против in-memory БД с синтетическими данными."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

from wb_pool.dashboard import (
    fmt_money,
    gather_dashboard_data,
    render_dashboard_html,
)
from wb_pool.db import apply_schema, create_engine


@pytest.fixture
async def engine():
    eng = create_engine("sqlite+aiosqlite://")
    await apply_schema(eng)
    now = int(datetime.now(UTC).timestamp())
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO raw_runs (run_id, started_at, command, status) "
                "VALUES ('t', 0, 'test', 'done')"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO cmp_groups (comparison_id, subject_id, nm_ids_json, "
                "period_start, period_end, created_at, expires_at, status, "
                "ingested_at, ingest_run_id) "
                "VALUES ('sub357-g111-p1-2', 357, '[111, 222]', 0, 0, :now, :now, "
                "'ready', 0, 1)"
            ),
            {"now": now},
        )
        for day in range(5):
            d = int(
                (datetime.now(UTC) - timedelta(days=day))
                .replace(hour=0, minute=0, second=0, microsecond=0)
                .timestamp()
            )
            for nm, kop in ((111, 5_000_000), (222, 100_000)):
                await conn.execute(
                    text(
                        "INSERT INTO cmp_funnel_daily (comparison_id, nm_id, date, "
                        "open_card_count, add_to_cart_count, orders_count, "
                        "orders_sum_kopeks, ingest_run_id) "
                        "VALUES ('sub357-g111-p1-2', :nm, :d, 100, 20, 5, :kop, 1)"
                    ),
                    {"nm": nm, "d": d, "kop": kop},
                )
        await conn.execute(
            text(
                "INSERT INTO cmp_search_query_per_nm (comparison_id, keyword, nm_id, "
                "period_start, period_end, cart_from_search_raw, order_from_search_raw, "
                "is_rounded_pct, snapshot_date, ingested_at, ingest_run_id) "
                "VALUES ('sub357-g111-p1-2', 'крем для ног', 111, 0, 0, 40, 7, 1, 0, 0, 1)"
            )
        )
        await conn.execute(
            text(
                "INSERT INTO cmp_warehouse_metrics (comparison_id, nm_id, "
                "warehouse_name, metric_type, metric_value, period_start, period_end, "
                "snapshot_date, ingest_run_id) "
                "VALUES ('sub357-g111-p1-2', 111, 'Коледино', 'stock', 350, 0, 0, 0, 1)"
            )
        )
    yield eng
    await eng.dispose()


async def test_gather_dashboard_data(engine):
    data = await gather_dashboard_data(engine, subject_id=357, days=30)
    assert data.pool_size == 2
    assert data.groups_count == 1
    # nm 111: 5 дней × 50 000 ₽ = 250 000 ₽/30д → tier "low"; nm 222 → trash
    assert data.tiers == {"sticky": 0, "mid": 0, "low": 1, "trash": 1}
    assert data.top_nm[0]["nm_id"] == 111
    assert len(data.trend) == 5
    assert data.top_keywords[0]["keyword"] == "крем для ног"
    assert data.warehouses == [("Коледино", 350)]


async def test_render_html_full_document(engine):
    data = await gather_dashboard_data(engine, subject_id=357, subject_name="Кремы")
    page = render_dashboard_html(data)
    assert page.startswith("<!doctype html>")
    assert "WB Pool" in page
    assert "Кремы" in page
    assert 'id="trend"' in page
    assert "крем для ног" in page
    assert "Коледино" in page
    # тема: dark-переопределения присутствуют
    assert 'prefers-color-scheme:dark' in page
    assert '[data-theme="dark"]' in page


async def test_render_html_fragment(engine):
    data = await gather_dashboard_data(engine, subject_id=357)
    frag = render_dashboard_html(data, full_document=False)
    assert not frag.startswith("<!doctype")
    assert "<title>" in frag


def test_fmt_money():
    assert fmt_money(123_456_789) == "1,23 млн ₽"
    assert fmt_money(12_345_600) == "123,5 тыс ₽"
    assert fmt_money(9_900) == "99 ₽"


async def test_empty_db_renders():
    eng = create_engine("sqlite+aiosqlite://")
    await apply_schema(eng)
    try:
        data = await gather_dashboard_data(eng)
        page = render_dashboard_html(data)
        assert "Нет данных" in page or "Недостаточно данных" in page
    finally:
        await eng.dispose()
