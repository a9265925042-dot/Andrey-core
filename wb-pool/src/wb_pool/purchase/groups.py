"""Purchase primitives: GroupPlan, comparison_id, atomic purchase, diff, chunking.

⚠ Каждая ПЕРВАЯ покупка уникальной группы 5 nm списывает 1 cabinet слот
НЕОБРАТИМО. Повторная покупка той же группы (same sorted nm + same period)
бесплатна через /limits.availableWithoutCharge.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool.clients.cabinet import COMPARISON_NMS_PATH, WBCabinetClient

GROUP_SIZE = 5


@dataclass(frozen=True)
class GroupPlan:
    nm_ids: list[int]


def compute_comparison_id(
    *, subject_id: int, plan: GroupPlan, period_start: date, period_end: date
) -> str:
    """Deterministic ID для idempotent purchase.

    Период входит в ID: другая дата = другой cmp_id = другой слот. Не меняй
    period для уже купленных групп (см. docs/05-purchase-pipeline.md).
    """
    nm_sorted = sorted(plan.nm_ids)
    first = nm_sorted[0]
    ps = period_start.strftime("%Y%m%d")
    pe = period_end.strftime("%Y%m%d")
    return f"sub{subject_id}-g{first}-p{ps}-{pe}"


async def purchase_group(
    *,
    client: WBCabinetClient,
    plan: GroupPlan,
    period_start: date,
    period_end: date,
) -> dict[str, Any]:
    """POST competitor-comparison/nms — атомарно списывает 1 слот для новой группы."""
    body = {
        "nmIDs": sorted(plan.nm_ids),
        "periodStart": period_start.isoformat(),
        "periodEnd": period_end.isoformat(),
    }
    return await client.post(COMPARISON_NMS_PATH, json=body)


async def diff_with_purchased(
    pool: list[int], *, engine: AsyncEngine
) -> tuple[list[int], list[int]]:
    """Returns (new_nm_to_buy, already_have_funnel_data).

    Lookup напрямую через cmp_funnel_daily — не через analytic_category_pool
    (см. docs/05-purchase-pipeline.md, шаг 1).
    """
    if not pool:
        return [], []
    async with engine.connect() as conn:
        placeholders = ",".join([f":n{i}" for i in range(len(pool))])
        params = {f"n{i}": nm for i, nm in enumerate(pool)}
        res = await conn.execute(
            text(
                f"SELECT DISTINCT nm_id FROM cmp_funnel_daily "
                f"WHERE nm_id IN ({placeholders})"
            ),
            params,
        )
        already = {int(row[0]) for row in res}
    new = [nm for nm in pool if nm not in already]
    return new, sorted(already & set(pool))


def chunk_with_padding(
    targets: list[int],
    padding_pool: list[int],
    *,
    group_size: int = GROUP_SIZE,
    rng_seed: int = 42,
) -> list[list[int]]:
    """Partition targets into groups of group_size; pad last short group.

    Padding из already-bought nm — refresh их данных бесплатно, а неполная
    группа стоит тот же 1 слот что и полная.
    """
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


__all__ = [
    "GROUP_SIZE",
    "GroupPlan",
    "chunk_with_padding",
    "compute_comparison_id",
    "diff_with_purchased",
    "purchase_group",
]
