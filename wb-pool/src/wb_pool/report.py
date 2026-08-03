"""Validation report — tier distribution, per-layer quality, coverage.

См. docs/07-validation.md. Tier thresholds (revenue 30d):
sticky >= 1M ₽, mid 500k-1M, low 100k-500k, trash < 100k.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

STICKY_KOPEKS = 100_000_000  # 1M ₽
MID_KOPEKS = 50_000_000  # 500k ₽
LOW_KOPEKS = 10_000_000  # 100k ₽


@dataclass
class ValidationReport:
    subject_id: int
    pool_size: int = 0
    tiers: dict[str, int] = field(
        default_factory=lambda: {"sticky": 0, "mid": 0, "low": 0, "trash": 0, "no_data": 0}
    )
    per_layer_only: dict[str, dict[str, int]] = field(default_factory=dict)
    top_new: list[dict[str, Any]] = field(default_factory=list)
    coverage: dict[str, Any] | None = None

    def summary(self) -> dict[str, Any]:
        return {
            "subject_id": self.subject_id,
            "pool_size": self.pool_size,
            "tiers": self.tiers,
        }


def _tier(revenue_kopeks: int) -> str:
    if revenue_kopeks >= STICKY_KOPEKS:
        return "sticky"
    if revenue_kopeks >= MID_KOPEKS:
        return "mid"
    if revenue_kopeks >= LOW_KOPEKS:
        return "low"
    return "trash"


async def revenue_30d_for_nms(
    engine: AsyncEngine, nm_ids: list[int]
) -> dict[int, int]:
    """SUM(orders_sum_kopeks) за последние 30 дней per nm из cmp_funnel_daily."""
    if not nm_ids:
        return {}
    cutoff = int((datetime.now(UTC) - timedelta(days=30)).timestamp())
    placeholders = ",".join([f":n{i}" for i in range(len(nm_ids))])
    params: dict[str, int] = {f"n{i}": nm for i, nm in enumerate(nm_ids)}
    params["cutoff"] = cutoff
    async with engine.connect() as conn:
        res = await conn.execute(
            text(
                f"SELECT nm_id, SUM(orders_sum_kopeks) FROM cmp_funnel_daily "
                f"WHERE nm_id IN ({placeholders}) AND date >= :cutoff "
                f"GROUP BY nm_id"
            ),
            params,
        )
        return {int(r[0]): int(r[1] or 0) for r in res}


async def build_report(
    engine: AsyncEngine,
    *,
    subject_id: int,
    pool_json_path: Path | None = None,
    known_before_nm_ids: set[int] | None = None,
) -> ValidationReport:
    report = ValidationReport(subject_id=subject_id)

    layer_membership: dict[int, list[str]] = {}
    pool_nm_ids: list[int] = []
    if pool_json_path is not None and pool_json_path.exists():
        data = json.loads(pool_json_path.read_text())
        pool_nm_ids = [int(x) for x in data.get("pool_nm_ids", [])]
        layer_membership = {
            int(k): list(v) for k, v in (data.get("layer_membership") or {}).items()
        }
    else:
        # fallback: все nm из купленных групп subject'а
        async with engine.connect() as conn:
            res = await conn.execute(
                text(
                    "SELECT nm_ids_json FROM cmp_groups "
                    "WHERE comparison_id LIKE :prefix"
                ),
                {"prefix": f"sub{subject_id}-%"},
            )
            seen: set[int] = set()
            for (nm_json,) in res:
                for nm in json.loads(nm_json):
                    seen.add(int(nm))
            pool_nm_ids = sorted(seen)

    report.pool_size = len(pool_nm_ids)
    revenue = await revenue_30d_for_nms(engine, pool_nm_ids)

    for nm in pool_nm_ids:
        if nm not in revenue:
            report.tiers["no_data"] += 1
        else:
            report.tiers[_tier(revenue[nm])] += 1

    # Per-layer only quality (карточки которые нашёл только один слой)
    for nm, layers in layer_membership.items():
        if len(layers) != 1 or nm not in revenue:
            continue
        layer = layers[0]
        bucket = report.per_layer_only.setdefault(layer, {"total": 0, "trash": 0})
        bucket["total"] += 1
        if revenue[nm] < MID_KOPEKS:
            bucket["trash"] += 1

    # Top-10 new by revenue
    known = known_before_nm_ids or set()
    ranked = sorted(
        ((nm, rev) for nm, rev in revenue.items() if nm not in known),
        key=lambda kv: -kv[1],
    )
    report.top_new = [
        {
            "nm_id": nm,
            "revenue_30d_mln_rub": round(rev / 100 / 1e6, 2),
            "sources": layer_membership.get(nm, []),
        }
        for nm, rev in ranked[:10]
    ]
    return report


def render_report(report: ValidationReport, *, subject_name: str = "") -> str:
    lines = [
        f"=== ALGORITHM REPORT — {subject_name} (subject {report.subject_id}) ===",
        "",
        f"Pool: {report.pool_size} nm",
        "",
        "Tier distribution (revenue last 30d):",
    ]
    labels = {
        "sticky": ">=1M RUB  sticky",
        "mid": "500k-1M   mid",
        "low": "100k-500k low",
        "trash": "<100k     trash",
        "no_data": "no cabinet data",
    }
    denom = max(1, report.pool_size)
    for key, label in labels.items():
        n = report.tiers.get(key, 0)
        lines.append(f"  {label}: {n:5d} ({100 * n // denom}%)")
    if report.per_layer_only:
        lines += ["", "Per-layer quality (only-this-layer, % under 500k):"]
        for layer, b in sorted(report.per_layer_only.items()):
            pct = 100 * b["trash"] // max(1, b["total"])
            lines.append(f"  {layer}: {pct}% ({b['trash']}/{b['total']})")
    if report.top_new:
        lines += ["", "Top-10 new (revenue 30d):"]
        for i, t in enumerate(report.top_new, 1):
            lines.append(
                f"  {i:2d}. nm={t['nm_id']}  rev={t['revenue_30d_mln_rub']:.2f} MRUB"
                f"  sources={t['sources']}"
            )
    if report.coverage:
        c = report.coverage
        lines += [
            "",
            "My carto coverage:",
            f"  Top-{c['my_top_total']} моих: {c['in_pool']}/{c['my_top_total']} в pool",
        ]
        if c.get("missing_nm_ids"):
            lines.append(f"  Missing: {c['missing_nm_ids']}")
    return "\n".join(lines)


__all__ = [
    "ValidationReport",
    "build_report",
    "render_report",
    "revenue_30d_for_nms",
]
