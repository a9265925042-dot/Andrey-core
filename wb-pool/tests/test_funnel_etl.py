"""Funnel ETL против in-memory SQLite: seed + upsert + idempotency + diff."""

import pytest
from sqlalchemy import text

from wb_pool.db import UnitOfWork, apply_schema, create_engine
from wb_pool.etl.funnel import (
    seed_cmp_group_from_payload,
    upsert_funnel_daily_from_comparison,
)
from wb_pool.purchase.groups import diff_with_purchased

PAYLOAD = {
    "data": {
        "period": {"start": "2026-02-19", "end": "2026-05-20"},
        "products": [
            {"product": {"nmId": 111}},
            {"product": {"nmId": 222}},
        ],
        "dayDynamics": [
            {
                "nmID": 111,
                "dt": "2026-05-19",
                "openCardCount": 100,
                "addToCartCount": 20,
                "orderCount": 5,
                "orderSum": 1234.56,
            },
            {
                "nmID": 222,
                "dt": "2026-05-19",
                "openCardCount": 50,
                "addToCartCount": 10,
                "orderCount": 2,
                "orderSum": 500.0,
            },
        ],
    }
}

CMP_ID = "sub357-g111-p20260219-20260520"


@pytest.fixture
async def engine():
    eng = create_engine("sqlite+aiosqlite://")
    await apply_schema(eng)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO raw_runs (run_id, started_at, command, status) "
                "VALUES ('test-run', 0, 'test', 'running')"
            )
        )
    yield eng
    await eng.dispose()


async def test_seed_and_upsert_funnel(engine):
    async with UnitOfWork(engine) as uow:
        await seed_cmp_group_from_payload(
            uow.session,
            comparison_id=CMP_ID,
            payload=PAYLOAD,
            ingest_run_id=1,
            category_job_id=None,  # extras принимаются как в cli_buy
        )
        etl = await upsert_funnel_daily_from_comparison(
            uow.session,
            comparison_id=CMP_ID,
            comparison_response=PAYLOAD,
            ingest_run_id=1,
        )
        await uow.commit()
    assert etl.rows_written == 2

    async with engine.connect() as conn:
        group = (
            await conn.execute(
                text(
                    "SELECT subject_id, nm_ids_json, status FROM cmp_groups "
                    "WHERE comparison_id = :c"
                ),
                {"c": CMP_ID},
            )
        ).one()
        assert group[0] == 357
        assert group[1] == "[111, 222]"
        assert group[2] == "ready"
        kopeks = (
            await conn.execute(
                text(
                    "SELECT orders_sum_kopeks FROM cmp_funnel_daily "
                    "WHERE comparison_id = :c AND nm_id = 111"
                ),
                {"c": CMP_ID},
            )
        ).scalar_one()
        assert kopeks == 123456  # рубли → копейки, int


async def test_upsert_idempotent(engine):
    for _ in range(2):
        async with UnitOfWork(engine) as uow:
            await seed_cmp_group_from_payload(
                uow.session, comparison_id=CMP_ID, payload=PAYLOAD, ingest_run_id=1
            )
            await upsert_funnel_daily_from_comparison(
                uow.session,
                comparison_id=CMP_ID,
                comparison_response=PAYLOAD,
                ingest_run_id=1,
            )
            await uow.commit()
    async with engine.connect() as conn:
        n_groups = (
            await conn.execute(text("SELECT COUNT(*) FROM cmp_groups"))
        ).scalar_one()
        n_rows = (
            await conn.execute(text("SELECT COUNT(*) FROM cmp_funnel_daily"))
        ).scalar_one()
    assert n_groups == 1
    assert n_rows == 2


async def test_diff_with_purchased(engine):
    async with UnitOfWork(engine) as uow:
        await seed_cmp_group_from_payload(
            uow.session, comparison_id=CMP_ID, payload=PAYLOAD, ingest_run_id=1
        )
        await upsert_funnel_daily_from_comparison(
            uow.session,
            comparison_id=CMP_ID,
            comparison_response=PAYLOAD,
            ingest_run_id=1,
        )
        await uow.commit()
    new, already = await diff_with_purchased([111, 222, 333, 444], engine=engine)
    assert new == [333, 444]
    assert already == [111, 222]
