"""Dashboard — self-contained HTML отчёт из локальной БД.

`wb-pool dashboard [--subject-id N] [--days 30] [--out data/dashboard.html]`

Всё inline (CSS + SVG + минимальный JS), без внешних зависимостей — файл
открывается локально и шарится как есть. Светлая/тёмная тема автоматически.
"""

from __future__ import annotations

import html
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from wb_pool.report import LOW_KOPEKS, MID_KOPEKS, STICKY_KOPEKS

# ---------------------------------------------------------------------------
# Data gathering
# ---------------------------------------------------------------------------


@dataclass
class DashboardData:
    subject_id: int | None
    subject_name: str
    days: int
    generated_at: str
    pool_size: int = 0
    groups_count: int = 0
    funnel_rows: int = 0
    revenue_total_kopeks: int = 0
    orders_total: int = 0
    dlq_unresolved: int = 0
    tiers: dict[str, int] = field(default_factory=dict)
    trend: list[tuple[str, int]] = field(default_factory=list)  # (date, kopeks)
    top_nm: list[dict[str, Any]] = field(default_factory=list)
    top_keywords: list[dict[str, Any]] = field(default_factory=list)
    warehouses: list[tuple[str, int]] = field(default_factory=list)
    layer_counts: dict[str, int] = field(default_factory=dict)


async def gather_dashboard_data(
    engine: AsyncEngine,
    *,
    subject_id: int | None = None,
    subject_name: str = "",
    days: int = 30,
    pool_json_path: Path | None = None,
) -> DashboardData:
    cutoff = int((datetime.now(UTC) - timedelta(days=days)).timestamp())
    data = DashboardData(
        subject_id=subject_id,
        subject_name=subject_name,
        days=days,
        generated_at=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
    )
    cmp_filter = ""
    params: dict[str, Any] = {"cutoff": cutoff}
    if subject_id is not None:
        cmp_filter = "AND cfd.comparison_id LIKE :prefix"
        params["prefix"] = f"sub{subject_id}-%"

    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    f"SELECT COUNT(DISTINCT cfd.nm_id), COUNT(*), "
                    f"COALESCE(SUM(cfd.orders_sum_kopeks), 0), "
                    f"COALESCE(SUM(cfd.orders_count), 0) "
                    f"FROM cmp_funnel_daily cfd "
                    f"WHERE cfd.date >= :cutoff {cmp_filter}"
                ),
                params,
            )
        ).one()
        data.pool_size, data.funnel_rows = int(row[0]), int(row[1])
        data.revenue_total_kopeks, data.orders_total = int(row[2]), int(row[3])

        group_params: dict[str, Any] = {}
        group_filter = ""
        if subject_id is not None:
            group_filter = "WHERE comparison_id LIKE :prefix"
            group_params["prefix"] = f"sub{subject_id}-%"
        data.groups_count = int(
            (
                await conn.execute(
                    text(f"SELECT COUNT(*) FROM cmp_groups {group_filter}"),
                    group_params,
                )
            ).scalar_one()
        )
        data.dlq_unresolved = int(
            (
                await conn.execute(
                    text("SELECT COUNT(*) FROM cmp_dlq WHERE resolved_at IS NULL")
                )
            ).scalar_one()
        )

        # Revenue per nm → tiers + top list
        res = await conn.execute(
            text(
                f"SELECT cfd.nm_id, SUM(cfd.orders_sum_kopeks), SUM(cfd.orders_count) "
                f"FROM cmp_funnel_daily cfd "
                f"WHERE cfd.date >= :cutoff {cmp_filter} "
                f"GROUP BY cfd.nm_id ORDER BY 2 DESC"
            ),
            params,
        )
        per_nm = [(int(r[0]), int(r[1] or 0), int(r[2] or 0)) for r in res]
        tiers = {"sticky": 0, "mid": 0, "low": 0, "trash": 0}
        for _nm, rev, _orders in per_nm:
            if rev >= STICKY_KOPEKS:
                tiers["sticky"] += 1
            elif rev >= MID_KOPEKS:
                tiers["mid"] += 1
            elif rev >= LOW_KOPEKS:
                tiers["low"] += 1
            else:
                tiers["trash"] += 1
        data.tiers = tiers

        layer_membership: dict[int, list[str]] = {}
        if pool_json_path is not None and pool_json_path.exists():
            pool = json.loads(pool_json_path.read_text())
            layer_membership = {
                int(k): list(v) for k, v in (pool.get("layer_membership") or {}).items()
            }
            counts: dict[str, int] = {}
            for layers in layer_membership.values():
                for layer in layers:
                    counts[layer] = counts.get(layer, 0) + 1
            data.layer_counts = counts

        data.top_nm = [
            {
                "nm_id": nm,
                "revenue_kopeks": rev,
                "orders": orders,
                "sources": layer_membership.get(nm, []),
            }
            for nm, rev, orders in per_nm[:20]
        ]

        # Daily trend
        res = await conn.execute(
            text(
                f"SELECT cfd.date, SUM(cfd.orders_sum_kopeks) "
                f"FROM cmp_funnel_daily cfd "
                f"WHERE cfd.date >= :cutoff {cmp_filter} "
                f"GROUP BY cfd.date ORDER BY cfd.date"
            ),
            params,
        )
        data.trend = [
            (datetime.fromtimestamp(int(r[0]), tz=UTC).strftime("%d.%m"), int(r[1] or 0))
            for r in res
        ]

        # Keywords
        kw_filter = ""
        kw_params: dict[str, Any] = {}
        if subject_id is not None:
            kw_filter = "WHERE comparison_id LIKE :prefix"
            kw_params["prefix"] = f"sub{subject_id}-%"
        res = await conn.execute(
            text(
                f"SELECT keyword, COALESCE(SUM(order_from_search_raw), 0) AS orders, "
                f"COALESCE(SUM(cart_from_search_raw), 0) AS carts, COUNT(DISTINCT nm_id) "
                f"FROM cmp_search_query_per_nm {kw_filter} "
                f"GROUP BY keyword ORDER BY orders DESC LIMIT 15"
            ),
            kw_params,
        )
        data.top_keywords = [
            {"keyword": str(r[0]), "orders": int(r[1]), "carts": int(r[2]), "nms": int(r[3])}
            for r in res
        ]

        # Warehouses (stock)
        res = await conn.execute(
            text(
                f"SELECT warehouse_name, SUM(metric_value) FROM cmp_warehouse_metrics "
                f"{'WHERE comparison_id LIKE :prefix AND' if subject_id is not None else 'WHERE'} "
                f"metric_type = 'stock' "
                f"GROUP BY warehouse_name ORDER BY 2 DESC LIMIT 10"
            ),
            kw_params,
        )
        data.warehouses = [(str(r[0]), int(r[1] or 0)) for r in res]
    return data


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------


def fmt_money(kopeks: int) -> str:
    rub = kopeks / 100
    if rub >= 1e6:
        return f"{rub / 1e6:.2f} млн ₽".replace(".", ",")
    if rub >= 1e3:
        return f"{rub / 1e3:.1f} тыс ₽".replace(".", ",")
    return f"{rub:.0f} ₽"


def fmt_int(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def _e(s: Any) -> str:
    return html.escape(str(s))


# ---------------------------------------------------------------------------
# SVG chart builders (inline, no deps)
# ---------------------------------------------------------------------------

_BAR_H = 20
_BAR_GAP = 10


def _rounded_bar_path(x: float, y: float, w: float, h: float, r: float = 4.0) -> str:
    """Bar с закруглённым data-концом, плоским у baseline (слева)."""
    r = min(r, w / 2, h / 2)
    return (
        f"M{x:.1f},{y:.1f} h{w - r:.1f} q{r:.1f},0 {r:.1f},{r:.1f} "
        f"v{h - 2 * r:.1f} q0,{r:.1f} -{r:.1f},{r:.1f} h-{w - r:.1f} z"
    )


def _truncate(label: str, max_chars: int) -> str:
    return label if len(label) <= max_chars else label[: max_chars - 1] + "…"


def svg_hbar(
    rows: list[tuple[str, int, str]],
    *,
    width: int = 680,
    label_w: int = 170,
    fill_var: str = "--series-1",
    value_fmt: str = "int",
) -> str:
    """Горизонтальный bar chart. rows = [(label, value, tooltip)].

    Длинные подписи усекаются с «…» — полное имя остаётся в tooltip.
    """
    if not rows:
        return '<p class="empty">Нет данных</p>'
    max_v = max(v for _, v, _ in rows) or 1
    plot_w = width - label_w - 90
    max_chars = max(6, (label_w - 10) // 7)  # ~7px на символ при 12px шрифте
    height = len(rows) * (_BAR_H + _BAR_GAP)
    parts = [
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'style="width:100%;height:auto;max-width:{width}px">'
    ]
    for i, (label, value, tip) in enumerate(rows):
        y = i * (_BAR_H + _BAR_GAP)
        w = max(2.0, plot_w * value / max_v)
        vtext = fmt_money(value) if value_fmt == "money" else fmt_int(value)
        parts.append(
            f'<text x="{label_w - 8}" y="{y + _BAR_H / 2 + 4}" text-anchor="end" '
            f'class="lbl" data-tip="{_e(tip)}">{_e(_truncate(label, max_chars))}</text>'
            f'<path d="{_rounded_bar_path(label_w, y + 1, w, _BAR_H - 2)}" '
            f'fill="var({fill_var})" data-tip="{_e(tip)}"></path>'
            f'<text x="{label_w + w + 8}" y="{y + _BAR_H / 2 + 4}" class="val">{_e(vtext)}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


def svg_trend(
    points: list[tuple[str, int]], *, width: int = 720, height: int = 220
) -> str:
    """Line + area тренд одной серии с crosshair-tooltip (vanilla JS)."""
    if len(points) < 2:
        return '<p class="empty">Недостаточно данных для тренда</p>'
    pad_l, pad_r, pad_t, pad_b = 56, 16, 12, 26
    pw, ph = width - pad_l - pad_r, height - pad_t - pad_b
    max_v = max(v for _, v in points) or 1
    step = pw / (len(points) - 1)
    coords = [
        (pad_l + i * step, pad_t + ph - ph * v / max_v)
        for i, (_, v) in enumerate(points)
    ]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{line} L{coords[-1][0]:.1f},{pad_t + ph} L{coords[0][0]:.1f},{pad_t + ph} Z"
    # 4 hairline gridlines + y labels
    grid = []
    for frac in (0.25, 0.5, 0.75, 1.0):
        gy = pad_t + ph - ph * frac
        grid.append(
            f'<line x1="{pad_l}" y1="{gy:.1f}" x2="{width - pad_r}" y2="{gy:.1f}" class="grid"/>'
            f'<text x="{pad_l - 8}" y="{gy + 4:.1f}" text-anchor="end" class="tick">'
            f"{_e(fmt_money(int(max_v * frac)))}</text>"
        )
    # x labels: first / middle / last
    xlab = []
    for i in (0, len(points) // 2, len(points) - 1):
        xlab.append(
            f'<text x="{coords[i][0]:.1f}" y="{height - 6}" text-anchor="middle" '
            f'class="tick">{_e(points[i][0])}</text>'
        )
    payload = json.dumps(
        [
            {"x": round(x, 1), "y": round(y, 1), "d": d, "v": fmt_money(v)}
            for (x, y), (d, v) in zip(coords, points, strict=True)
        ],
        ensure_ascii=False,
    )
    return (
        f'<svg id="trend" viewBox="0 0 {width} {height}" role="img" '
        f'style="width:100%;height:auto;max-width:{width}px" '
        f"data-points='{_e(payload)}'>"
        + "".join(grid)
        + f'<line x1="{pad_l}" y1="{pad_t + ph}" x2="{width - pad_r}" y2="{pad_t + ph}" class="axis"/>'
        + f'<path d="{area}" fill="var(--series-1)" opacity="0.12"/>'
        + f'<path d="{line}" fill="none" stroke="var(--series-1)" stroke-width="2"/>'
        + f'<line id="xh" x1="0" y1="{pad_t}" x2="0" y2="{pad_t + ph}" class="crosshair" visibility="hidden"/>'
        + '<circle id="xh-dot" r="4" fill="var(--series-1)" stroke="var(--surface-1)" '
        'stroke-width="2" visibility="hidden"/>'
        + "".join(xlab)
        + "</svg>"
    )


def _tier_bar(tiers: dict[str, int]) -> str:
    """Tier distribution — ordinal ramp одного hue (синий, 550→250)."""
    labels = [
        ("sticky", "≥ 1 млн ₽ (sticky)", "--seq-550"),
        ("mid", "500 тыс – 1 млн (mid)", "--seq-450"),
        ("low", "100–500 тыс (low)", "--seq-350"),
        ("trash", "< 100 тыс (trash)", "--seq-250"),
    ]
    rows = [
        (label, tiers.get(key, 0), f"{label}: {tiers.get(key, 0)} карточек")
        for key, label, _ in labels
    ]
    if not any(v for _, v, _ in rows):
        return '<p class="empty">Нет данных</p>'
    max_v = max(v for _, v, _ in rows) or 1
    width, label_w = 680, 210
    plot_w = width - label_w - 70
    parts = [
        f'<svg viewBox="0 0 {width} {4 * (_BAR_H + _BAR_GAP)}" role="img" '
        f'style="width:100%;height:auto;max-width:{width}px">'
    ]
    for i, ((_key, label, var), (_, value, tip)) in enumerate(
        zip(labels, rows, strict=True)
    ):
        y = i * (_BAR_H + _BAR_GAP)
        w = max(2.0, plot_w * value / max_v)
        parts.append(
            f'<text x="{label_w - 8}" y="{y + _BAR_H / 2 + 4}" text-anchor="end" '
            f'class="lbl">{_e(label)}</text>'
            f'<path d="{_rounded_bar_path(label_w, y + 1, w, _BAR_H - 2)}" '
            f'fill="var({var})" data-tip="{_e(tip)}"></path>'
            f'<text x="{label_w + w + 8}" y="{y + _BAR_H / 2 + 4}" class="val">{value}</text>'
        )
    parts.append("</svg>")
    return "".join(parts)


# ---------------------------------------------------------------------------
# Page assembly
# ---------------------------------------------------------------------------

_CSS = """
.dash{color-scheme:light;
  --surface-1:#fcfcfb;--page:#f9f9f7;--text-1:#0b0b0b;--text-2:#52514e;
  --muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:rgba(11,11,11,.10);
  --series-1:#2a78d6;
  --seq-250:#86b6ef;--seq-350:#5598e7;--seq-450:#2a78d6;--seq-550:#1c5cab;
  --good:#006300;--critical:#d03b3b;
  font-family:system-ui,-apple-system,"Segoe UI",sans-serif;
  background:var(--page);color:var(--text-1);margin:0;padding:24px;
  min-height:100vh;box-sizing:border-box}
@media (prefers-color-scheme:dark){
  :root:where(:not([data-theme="light"])) .dash{color-scheme:dark;
    --surface-1:#1a1a19;--page:#0d0d0d;--text-1:#fff;--text-2:#c3c2b7;
    --muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
    --series-1:#3987e5;
    --seq-250:#104281;--seq-350:#184f95;--seq-450:#256abf;--seq-550:#3987e5;
    --good:#0ca30c;--critical:#d03b3b}}
:root[data-theme="dark"] .dash{color-scheme:dark;
  --surface-1:#1a1a19;--page:#0d0d0d;--text-1:#fff;--text-2:#c3c2b7;
  --muted:#898781;--grid:#2c2c2a;--axis:#383835;--border:rgba(255,255,255,.10);
  --series-1:#3987e5;
  --seq-250:#104281;--seq-350:#184f95;--seq-450:#256abf;--seq-550:#3987e5;
  --good:#0ca30c;--critical:#d03b3b}
.dash h1{font-size:20px;margin:0 0 2px}
.dash .sub{color:var(--text-2);font-size:13px;margin:0 0 20px}
.dash .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));
  gap:12px;margin-bottom:20px}
.dash .tile{background:var(--surface-1);border:1px solid var(--border);
  border-radius:10px;padding:14px 16px}
.dash .tile .k{font-size:12px;color:var(--text-2);margin-bottom:6px}
.dash .tile .v{font-size:24px;font-weight:600;line-height:1.15;white-space:nowrap}
.dash .tile .d{font-size:12px;color:var(--muted);margin-top:4px}
.dash .tile .v.bad{color:var(--critical)}
.dash .grid2{display:grid;grid-template-columns:1fr;gap:16px}
@media(min-width:960px){.dash .grid2{grid-template-columns:1fr 1fr}}
.dash .card{background:var(--surface-1);border:1px solid var(--border);
  border-radius:10px;padding:16px 18px;margin-bottom:16px;overflow-x:auto}
.dash .card h2{font-size:14px;margin:0 0 12px;font-weight:600}
.dash svg .lbl{font-size:12px;fill:var(--text-2)}
.dash svg .val{font-size:12px;fill:var(--text-2);font-variant-numeric:tabular-nums}
.dash svg .tick{font-size:11px;fill:var(--muted);font-variant-numeric:tabular-nums}
.dash svg .grid{stroke:var(--grid);stroke-width:1}
.dash svg .axis{stroke:var(--axis);stroke-width:1}
.dash svg .crosshair{stroke:var(--axis);stroke-width:1;stroke-dasharray:3 3}
.dash svg path[data-tip]:hover{opacity:.85}
.dash table{border-collapse:collapse;width:100%;font-size:13px}
.dash th{text-align:left;color:var(--muted);font-weight:500;font-size:12px;
  padding:6px 10px;border-bottom:1px solid var(--grid)}
.dash td{padding:6px 10px;border-bottom:1px solid var(--grid)}
.dash td.num,.dash th.num{text-align:right;font-variant-numeric:tabular-nums}
.dash .src{display:inline-block;font-size:10px;color:var(--text-2);
  border:1px solid var(--border);border-radius:8px;padding:0 6px;margin-left:4px}
.dash .empty{color:var(--muted);font-size:13px}
.dash .foot{color:var(--muted);font-size:11px;margin-top:8px}
#tip{position:fixed;pointer-events:none;background:var(--surface-1,#fff);
  border:1px solid rgba(11,11,11,.15);border-radius:6px;padding:6px 10px;
  font:12px system-ui,sans-serif;box-shadow:0 2px 8px rgba(0,0,0,.12);
  visibility:hidden;z-index:10;white-space:nowrap}
"""

_JS = """
(function(){
  var tip=document.createElement('div');tip.id='tip';document.body.appendChild(tip);
  function show(t,x,y){tip.textContent=t;tip.style.visibility='visible';
    tip.style.left=Math.min(x+14,window.innerWidth-tip.offsetWidth-8)+'px';
    tip.style.top=(y+14)+'px';}
  function hide(){tip.style.visibility='hidden';}
  document.querySelectorAll('[data-tip]').forEach(function(el){
    el.addEventListener('mousemove',function(e){show(el.getAttribute('data-tip'),e.clientX,e.clientY);});
    el.addEventListener('mouseleave',hide);
  });
  var svg=document.getElementById('trend');
  if(svg){
    var pts=JSON.parse(svg.getAttribute('data-points'));
    var xh=svg.getElementById?svg.getElementById('xh'):document.getElementById('xh');
    var dot=document.getElementById('xh-dot');
    svg.addEventListener('mousemove',function(e){
      var r=svg.getBoundingClientRect();
      var vb=svg.viewBox.baseVal;
      var mx=(e.clientX-r.left)*vb.width/r.width;
      var best=pts[0],bd=1e9;
      pts.forEach(function(p){var d=Math.abs(p.x-mx);if(d<bd){bd=d;best=p;}});
      xh.setAttribute('x1',best.x);xh.setAttribute('x2',best.x);
      xh.setAttribute('visibility','visible');
      dot.setAttribute('cx',best.x);dot.setAttribute('cy',best.y);
      dot.setAttribute('visibility','visible');
      show(best.d+' — '+best.v,e.clientX,e.clientY);
    });
    svg.addEventListener('mouseleave',function(){
      xh.setAttribute('visibility','hidden');
      dot.setAttribute('visibility','hidden');hide();
    });
  }
})();
"""


def render_dashboard_html(data: DashboardData, *, full_document: bool = True) -> str:
    title = "WB Pool — дашборд"
    scope = (
        f"subject {data.subject_id}"
        + (f" · {data.subject_name}" if data.subject_name else "")
        if data.subject_id is not None
        else "все категории"
    )
    sticky_share = (
        f"{100 * data.tiers.get('sticky', 0) // max(1, data.pool_size)}%"
        if data.pool_size
        else "—"
    )

    kpis = [
        ("Карточек в pool", fmt_int(data.pool_size), f"за {data.days} дн", ""),
        ("Выручка pool (30д)", fmt_money(data.revenue_total_kopeks), f"{fmt_int(data.orders_total)} заказов", ""),
        ("Sticky-доля", sticky_share, "карточки ≥ 1 млн ₽/30д", ""),
        ("Куплено групп", fmt_int(data.groups_count), "5 nm = 1 слот", ""),
        ("DLQ", fmt_int(data.dlq_unresolved), "failed purchases", "bad" if data.dlq_unresolved else ""),
    ]
    kpi_html = "".join(
        f'<div class="tile"><div class="k">{_e(k)}</div>'
        f'<div class="v {cls}">{_e(v)}</div><div class="d">{_e(d)}</div></div>'
        for k, v, d, cls in kpis
    )

    top_nm_rows = [
        (
            f"nm {r['nm_id']}",
            r["revenue_kopeks"],
            f"nm {r['nm_id']}: {fmt_money(r['revenue_kopeks'])}, "
            f"{fmt_int(r['orders'])} заказов"
            + (f" · {'+'.join(r['sources'])}" if r["sources"] else ""),
        )
        for r in data.top_nm
    ]
    wh_rows = [
        (name, v, f"{name}: {fmt_int(v)} шт") for name, v in data.warehouses
    ]
    layer_rows = [
        (
            {"serp_main": "SERP main", "serp_narrow": "SERP narrow", "mpstats": "MPStats", "db_earner": "DB earners"}.get(k, k),
            v,
            f"{k}: {v} карточек",
        )
        for k, v in sorted(data.layer_counts.items(), key=lambda kv: -kv[1])
    ]

    kw_table = (
        "<table><thead><tr><th>Запрос</th><th class='num'>Заказы</th>"
        "<th class='num'>Корзины</th><th class='num'>Карточек</th></tr></thead><tbody>"
        + "".join(
            f"<tr><td>{_e(k['keyword'])}</td><td class='num'>{fmt_int(k['orders'])}</td>"
            f"<td class='num'>{fmt_int(k['carts'])}</td><td class='num'>{k['nms']}</td></tr>"
            for k in data.top_keywords
        )
        + "</tbody></table>"
        if data.top_keywords
        else '<p class="empty">Нет данных — запусти wb-pool ingest-excel</p>'
    )

    body = f"""
<div class="dash">
  <h1>{_e(title)}</h1>
  <p class="sub">{_e(scope)} · последние {data.days} дн · сгенерировано {_e(data.generated_at)}</p>
  <div class="kpis">{kpi_html}</div>

  <div class="card"><h2>Выручка pool по дням</h2>{svg_trend(data.trend)}</div>

  <div class="grid2">
    <div class="card"><h2>Tier distribution (выручка 30д)</h2>{_tier_bar(data.tiers)}</div>
    <div class="card"><h2>Слои discovery (карточек нашёл слой)</h2>{
        svg_hbar(layer_rows) if layer_rows
        else '<p class="empty">Нет pool JSON — передай --pool-json</p>'
    }</div>
  </div>

  <div class="card"><h2>Топ-20 карточек по выручке (30д)</h2>{
      svg_hbar(top_nm_rows, value_fmt="money", label_w=130)
  }</div>

  <div class="grid2">
    <div class="card"><h2>Топ поисковых запросов (заказы из поиска)</h2>{kw_table}</div>
    <div class="card"><h2>Склады конкурентов (остатки, шт)</h2>{
        svg_hbar(wh_rows) if wh_rows
        else '<p class="empty">Нет данных — запусти wb-pool ingest-excel</p>'
    }</div>
  </div>

  <p class="foot">wb-pool dashboard · данные: cmp_funnel_daily / cmp_search_query_per_nm /
  cmp_warehouse_metrics · цены в БД — копейки, на графиках — ₽</p>
</div>
<script>{_JS}</script>
"""
    if not full_document:
        return f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n{body}"
    return (
        "<!doctype html>\n<html lang=\"ru\">\n<head>\n<meta charset=\"utf-8\">\n"
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{_e(title)}</title>\n<style>{_CSS}</style>\n</head>\n"
        f"<body style=\"margin:0\">{body}</body>\n</html>\n"
    )


__all__ = [
    "DashboardData",
    "fmt_int",
    "fmt_money",
    "gather_dashboard_data",
    "render_dashboard_html",
]
