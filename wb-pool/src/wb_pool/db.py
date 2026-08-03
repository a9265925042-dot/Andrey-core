"""SQLAlchemy async engine + UnitOfWork + schema bootstrap.

`wb-pool migrate` использует :func:`apply_schema` (create_all + views + WAL
pragma). Для production-масштаба можно перейти на alembic autogenerate — ORM
models уже описывают полную схему (см. docs/db.sql для справки).
"""

from __future__ import annotations

from types import TracebackType
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

_VIEWS_SQL = [
    """
    CREATE VIEW IF NOT EXISTS v_cmp_funnel_with_conversions AS
    SELECT
      cfd.*,
      CAST(add_to_cart_count AS REAL) / NULLIF(open_card_count, 0) AS cart_conv_pct,
      CAST(orders_count AS REAL) / NULLIF(open_card_count, 0) AS order_conv_pct,
      CAST(orders_count AS REAL) / NULLIF(add_to_cart_count, 0) AS cart_to_order_pct
    FROM cmp_funnel_daily cfd
    """,
    """
    CREATE VIEW IF NOT EXISTS v_revenue_30d_per_nm AS
    SELECT
      nm_id,
      SUM(orders_sum_kopeks) AS revenue_30d_kopeks,
      SUM(orders_count) AS orders_30d,
      CAST(SUM(orders_sum_kopeks) AS REAL) / 100 / 1e6 AS revenue_30d_mln_rub
    FROM cmp_funnel_daily
    WHERE date >= (strftime('%s', 'now', '-30 days'))
    GROUP BY nm_id
    """,
]


def create_engine(database_url: str, **kwargs: Any) -> AsyncEngine:
    return create_async_engine(database_url, future=True, echo=False, **kwargs)


class UnitOfWork:
    def __init__(self, engine: AsyncEngine):
        self._engine = engine
        self._session_factory = async_sessionmaker(engine, expire_on_commit=False)
        self.session: AsyncSession = None  # type: ignore[assignment]

    async def __aenter__(self) -> UnitOfWork:
        self.session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        await self.session.commit()


async def apply_schema(engine: AsyncEngine) -> None:
    """Idempotent schema bootstrap: tables (ORM metadata) + views + pragmas."""
    from wb_pool.models import Base

    is_sqlite = engine.url.get_backend_name() == "sqlite"
    async with engine.begin() as conn:
        if is_sqlite:
            await conn.execute(text("PRAGMA journal_mode = WAL"))
            await conn.execute(text("PRAGMA foreign_keys = ON"))
            await conn.execute(text("PRAGMA synchronous = NORMAL"))
        await conn.run_sync(Base.metadata.create_all)
        if is_sqlite:
            for view_sql in _VIEWS_SQL:
                await conn.execute(text(view_sql))
