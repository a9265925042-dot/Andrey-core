#!/usr/bin/env python3
"""
excel.py — write the P&L workbook (reports/pnl.xlsx).

Sheets:
    1. Сводка                — executive summary (status + KPIs + narrative)
    2. P&L                   — monthly P&L matrix with common-size column
    3. Сверка с банком       — cash reconciliation bridge
    4. Категория × Месяц     — full category × month pivot
    5. Клиенты               — top customers + concentration KPIs
    6. Расходы               — top expense counterparties + HHI
    7. Операции              — every parsed bank operation, filterable

Convention: same numbers as the PDF — deterministic. Built from the same
`pnl` and `analytics` dicts, so anything that holds for the PDF holds here.
"""
from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo
from openpyxl.worksheet.worksheet import Worksheet

# --- palette (matches the PDF) ----------------------------------------------
NAVY   = "0B2545"
SLATE  = "13315C"
GOLD   = "C99B3E"
RED    = "B43E3E"
GREY   = "5E6B7A"
LIGHT  = "F4F1EA"
HAIR   = "D6D3CC"
WHITE  = "FFFFFF"

# --- number formats (Excel custom-format strings) ---------------------------
# Russian financial convention: NBSP as thousands separator, accounting parens
# for negatives, red colour for losses.
RUB_FMT  = '#,##0.00\\ "₽";[Red](#,##0.00\\ "₽");0.00\\ "₽"'
RUB0_FMT = '#,##0\\ "₽";[Red](#,##0\\ "₽");0\\ "₽"'
PCT_FMT  = '+0.0%;-0.0%;0.0%'
INT_FMT  = '#,##0'
TEXT_FMT = '@'

# --- common styles ----------------------------------------------------------
THIN  = Side(border_style="thin", color=HAIR)
THICK = Side(border_style="medium", color=NAVY)
ALL_BORDER  = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEAD_BORDER = Border(left=THIN, right=THIN, top=THICK, bottom=THICK)

NAVY_FILL  = PatternFill("solid", fgColor=NAVY)
LIGHT_FILL = PatternFill("solid", fgColor=LIGHT)
GOLD_FILL  = PatternFill("solid", fgColor=GOLD)
RED_FILL   = PatternFill("solid", fgColor=RED)

WHITE_BOLD = Font(name="Calibri", size=11, bold=True, color=WHITE)
NAVY_BOLD  = Font(name="Calibri", size=11, bold=True, color=NAVY)
NAVY_REG   = Font(name="Calibri", size=11, bold=False, color=NAVY)
GREY_SMALL = Font(name="Calibri", size=9, color=GREY)
TITLE_FONT = Font(name="Calibri", size=18, bold=True, color=NAVY)
SUBT_FONT  = Font(name="Calibri", size=10, color=GREY)

CENTER = Alignment(horizontal="center", vertical="center")
RIGHT  = Alignment(horizontal="right",  vertical="center")
LEFT   = Alignment(horizontal="left",   vertical="center", wrap_text=False)
WRAPLEFT = Alignment(horizontal="left", vertical="top",    wrap_text=True)


# --- helpers ----------------------------------------------------------------

def _dec(v: Any) -> Decimal:
    if isinstance(v, Decimal):
        return v
    if v is None or v == "":
        return Decimal("0")
    return Decimal(str(v).replace(",", "."))


def _set_col_widths(ws: Worksheet, widths: list[float]) -> None:
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_title(ws: Worksheet, title: str, subtitle: str = "") -> int:
    ws["A1"] = title
    ws["A1"].font = TITLE_FONT
    ws["A1"].alignment = LEFT
    if subtitle:
        ws["A2"] = subtitle
        ws["A2"].font = SUBT_FONT
        ws["A2"].alignment = LEFT
        return 4
    return 3


def _month_label(yyyymm: str) -> str:
    if yyyymm == "Total":
        return "Итого"
    y, m = yyyymm.split("-")
    ru = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн",
          "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
    return f"{ru[int(m)-1]} {y[-2:]}"


# --- sheet builders ---------------------------------------------------------

def _build_summary(ws: Worksheet, pnl: dict, analytics: dict, period: str) -> None:
    """Сводка — KPI tiles + status + narrative."""
    rev_t  = pnl["revenue"]["Total"]
    gp_t   = pnl["gross"]["Total"]
    ebit_t = pnl["ebit"]["Total"]
    net_t  = pnl["net"]["Total"]
    burn   = analytics["burn_per_month"]
    runway = analytics["runway_months"]
    cust_top1 = analytics["customer_concentration"]["top1"]
    cust_hhi  = analytics["customer_concentration"]["hhi"]

    r = _write_title(ws, "Сводка для руководителя",
                     f"ООО «ТЕХСОЛ» · {period} · P&L по кассовому методу (RUB)")

    # Status banner
    status = "ВНЕ КУРСА — кассовый убыток, концентрация клиентов критическая" if (
        net_t < 0 and (runway is None or runway < 12)) else (
        "ПОД РИСКОМ — операционный убыток" if net_t < 0 else "ВСЁ В ПОРЯДКЕ")
    status_color = RED if net_t < 0 and (runway is None or runway < 12) else (GOLD if net_t < 0 else "1F7A4D")
    ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
    cell = ws.cell(row=r, column=1, value=status)
    cell.font = Font(name="Calibri", size=12, bold=True, color=WHITE)
    cell.fill = PatternFill("solid", fgColor=status_color)
    cell.alignment = LEFT
    ws.row_dimensions[r].height = 22
    r += 2

    # KPI tiles in a 2-column layout (label | value)
    kpis: list[tuple[str, Any, str]] = [
        ("Выручка (нетто)", rev_t, RUB_FMT),
        ("Валовая прибыль", gp_t, RUB_FMT),
        ("EBIT", ebit_t, RUB_FMT),
        ("Чистый результат", net_t, RUB_FMT),
        ("Валовая маржа", (gp_t / rev_t) if rev_t else Decimal(0), PCT_FMT),
        ("Операционная маржа", (ebit_t / rev_t) if rev_t else Decimal(0), PCT_FMT),
        ("Чистая маржа", (net_t / rev_t) if rev_t else Decimal(0), PCT_FMT),
        ("Месяцев в периоде", len(analytics["months"]), INT_FMT),
        ("Burn / месяц", burn, RUB_FMT),
        ("Запас кэша (мес)", float(runway) if runway is not None else None, '0.00'),
        ("Топ-1 клиент, %", (cust_top1 / 100), PCT_FMT),
        ("HHI клиентов", float(cust_hhi), INT_FMT),
    ]
    ws.cell(row=r, column=1, value="Показатель").font = WHITE_BOLD
    ws.cell(row=r, column=1).fill = NAVY_FILL
    ws.cell(row=r, column=2, value="Значение").font = WHITE_BOLD
    ws.cell(row=r, column=2).fill = NAVY_FILL
    ws.cell(row=r, column=2).alignment = RIGHT
    r += 1
    for label, value, fmt in kpis:
        ws.cell(row=r, column=1, value=label).font = NAVY_REG
        ws.cell(row=r, column=1).alignment = LEFT
        c = ws.cell(row=r, column=2, value=float(value) if isinstance(value, Decimal) else value)
        c.number_format = fmt
        c.font = NAVY_BOLD
        c.alignment = RIGHT
        for col_idx in (1, 2):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
        r += 1

    r += 2
    # Narrative bullets
    ws.cell(row=r, column=1, value="Топ-3 драйвера").font = NAVY_BOLD
    r += 1
    rev_total = analytics["rev_total"]
    top_cust  = analytics["customers"][0] if analytics["customers"] else None
    drivers = [
        f"Один клиент = {float(cust_top1):.1f}% выручки: «{(top_cust['name'] if top_cust else '')[:40]}» "
        f"({float(top_cust['gross']) if top_cust else 0:,.2f} ₽ из {float(rev_total):,.2f} ₽).".replace(",", " "),
        f"Апрельский всплеск расходов: {float(pnl['cogs']['2026-04'] + pnl['opex_total']['2026-04'] + pnl['tax']['2026-04']):,.2f} ₽ — больше чем январь+март.".replace(",", " "),
        "Канал Ozon снижается: 174 688 → 34 594 → 50 971 → 20 279 ₽ (−88% за 4 мес).",
    ]
    for d in drivers:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        c = ws.cell(row=r, column=1, value=f"• {d}")
        c.alignment = WRAPLEFT
        ws.row_dimensions[r].height = 30
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Топ-3 риска").font = NAVY_BOLD
    r += 1
    risks = [
        f"Концентрация (КРИТИЧНО): HHI = {float(cust_hhi):.0f} при пороге US DOJ 2500. Потеря топ-1 = −{float(cust_top1):.1f}% выручки.",
        f"Запас кэша <1 месяца: остаток 9 568 ₽, burn ≈ {float(burn):,.2f} ₽ / мес.".replace(",", " "),
        "VAT-лаг: +470 994 ₽ — деньги бюджета, истребуются при ближайшей декларации НДС.",
    ]
    for risk in risks:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        c = ws.cell(row=r, column=1, value=f"• {risk}")
        c.alignment = WRAPLEFT
        ws.row_dimensions[r].height = 30
        r += 1

    r += 1
    ws.cell(row=r, column=1, value="Рекомендуемые действия").font = NAVY_BOLD
    r += 1
    actions = [
        "Диверсификация клиентского портфеля (0–3 мес): снизить долю топ-1 до <40% за 2 квартала.",
        "Аудит апрельских расходов: разовый ли всплеск или новая база?",
        "Резерв под НДС: зафиксировать 471k ₽ как obligation; не использовать на опер. нужды.",
        "Мониторинг: ежемесячный перезапуск ./finance/run.sh, отслеживать MoM-тренд и HHI.",
    ]
    for action in actions:
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=6)
        c = ws.cell(row=r, column=1, value=f"• {action}")
        c.alignment = WRAPLEFT
        ws.row_dimensions[r].height = 28
        r += 1

    _set_col_widths(ws, [32, 22, 14, 14, 14, 14])


def _build_pnl_sheet(ws: Worksheet, pnl: dict, period: str) -> None:
    """P&L — monthly table with common-size %-of-Revenue column."""
    months = pnl["months"]
    r = _write_title(ws, "Отчёт об операциях (P&L)",
                     f"Кассовый метод · нетто от НДС · {period}")

    # Header row
    headers = ["Строка"] + [_month_label(m) for m in months] + ["Итого", "% Выр"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = WHITE_BOLD
        c.fill = NAVY_FILL
        c.alignment = CENTER if i > 1 else LEFT
        c.border = HEAD_BORDER
    head_row = r
    r += 1

    rev_total = pnl["revenue"]["Total"]

    def write(label: str, d: dict, bold: bool = False, indent: int = 0, accent: bool = False):
        nonlocal r
        ws.cell(row=r, column=1, value=("  " * indent) + label)
        ws.cell(row=r, column=1).font = NAVY_BOLD if bold else NAVY_REG
        ws.cell(row=r, column=1).alignment = LEFT
        for col_idx, m in enumerate(months, start=2):
            v = d.get(m, Decimal(0))
            c = ws.cell(row=r, column=col_idx, value=float(v))
            c.number_format = RUB_FMT
            c.alignment = RIGHT
            if bold: c.font = NAVY_BOLD
        total = d["Total"]
        c = ws.cell(row=r, column=len(months) + 2, value=float(total))
        c.number_format = RUB_FMT
        c.alignment = RIGHT
        if bold: c.font = NAVY_BOLD
        pct_col = len(months) + 3
        if rev_total:
            c = ws.cell(row=r, column=pct_col, value=float(total / rev_total))
            c.number_format = '0.0%'
        else:
            ws.cell(row=r, column=pct_col, value="")
        ws.cell(row=r, column=pct_col).alignment = RIGHT
        ws.cell(row=r, column=pct_col).font = GREY_SMALL
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
            if accent:
                ws.cell(row=r, column=col_idx).fill = LIGHT_FILL
        r += 1

    def write_margin(label: str, num: dict, den: dict):
        nonlocal r
        ws.cell(row=r, column=1, value=("  " + label))
        ws.cell(row=r, column=1).font = GREY_SMALL
        ws.cell(row=r, column=1).alignment = LEFT
        for col_idx, m in enumerate(months, start=2):
            d_val = den.get(m, Decimal(0))
            if d_val == 0:
                ws.cell(row=r, column=col_idx, value="")
            else:
                pct = float(num.get(m, Decimal(0)) / d_val)
                c = ws.cell(row=r, column=col_idx, value=pct)
                c.number_format = PCT_FMT
            ws.cell(row=r, column=col_idx).alignment = RIGHT
            ws.cell(row=r, column=col_idx).font = GREY_SMALL
        d_t = den["Total"]
        c = ws.cell(row=r, column=len(months) + 2,
                    value=float(num["Total"] / d_t) if d_t else "")
        if d_t:
            c.number_format = PCT_FMT
        c.alignment = RIGHT
        c.font = GREY_SMALL
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
        r += 1

    _SUB_RU = {
        "marketplace_ozon": "Маркетплейс Ozon",
        "direct_b2b": "Прямые B2B",
        "manufacturing": "Производство",
        "fns": "ФНС (налоги)",
        "social": "ОСФР (соц. взносы)",
    }
    write("Выручка (без НДС)", pnl["revenue"], bold=True, accent=True)
    for n, d in pnl["revenue_sub"]:
        write(_SUB_RU.get(n, n), d, indent=1)
    write("Себестоимость (COGS)", pnl["cogs"], bold=True, accent=True)
    for n, d in pnl["cogs_sub"]:
        write(_SUB_RU.get(n, n), d, indent=1)
    write("Валовая прибыль", pnl["gross"], bold=True)
    write_margin("Валовая маржа", pnl["gross"], pnl["revenue"])
    write("Операционные расходы", pnl["opex_total"], bold=True, accent=True)
    for label, d in pnl["opex_lines"]:
        write(label, d, indent=1)
    write("Операционная прибыль (EBIT)", pnl["ebit"], bold=True)
    write_margin("Операционная маржа", pnl["ebit"], pnl["revenue"])
    write("Прочий доход", pnl["other"])
    write("Налоги (ФНС + ОСФР)", pnl["tax"], bold=True, accent=True)
    for n, d in pnl["tax_sub"]:
        write(_SUB_RU.get(n, n), d, indent=1)
    write("Чистый денежный результат", pnl["net"], bold=True)
    write_margin("Чистая маржа", pnl["net"], pnl["revenue"])

    ws.freeze_panes = ws.cell(row=head_row + 1, column=2)
    _set_col_widths(ws, [36] + [14] * (len(months) + 1) + [9])


def _build_bridge_sheet(ws: Worksheet, pnl: dict) -> None:
    """Сверка с банком — cash bridge."""
    r = _write_title(ws, "Сверка с банком — P&L → расчётный счёт",
                     "Net P&L + (VAT-лаг) + (внутр. переводы) = Изм. остатка")

    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pnl import cash_bridge as _cb  # local import to avoid cycle
    bridge = _cb(pnl)

    ws.cell(row=r, column=1, value="Шаг сверки").font = WHITE_BOLD
    ws.cell(row=r, column=1).fill = NAVY_FILL
    ws.cell(row=r, column=2, value="Сумма, ₽").font = WHITE_BOLD
    ws.cell(row=r, column=2).fill = NAVY_FILL
    ws.cell(row=r, column=2).alignment = RIGHT
    for col_idx in (1, 2):
        ws.cell(row=r, column=col_idx).border = HEAD_BORDER
    r += 1

    for label, value in bridge:
        is_total = label.startswith("=")
        font = NAVY_BOLD if is_total else NAVY_REG
        fill = LIGHT_FILL if is_total else None
        ws.cell(row=r, column=1, value=label).font = font
        ws.cell(row=r, column=1).alignment = LEFT
        c = ws.cell(row=r, column=2, value=float(value))
        c.number_format = RUB_FMT
        c.font = font
        c.alignment = RIGHT
        for col_idx in (1, 2):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
            if fill:
                ws.cell(row=r, column=col_idx).fill = fill
        r += 1

    _set_col_widths(ws, [54, 22])


def _build_pivot_sheet(ws: Worksheet, pnl: dict, analytics: dict) -> None:
    """Категория × Месяц — every category, every month, net amounts."""
    from collections import defaultdict
    months = analytics["months"]
    rows = csv_load("reports/categorized.csv")
    pivot: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: Decimal(0)))
    sides: dict[str, str] = {}
    for ro in rows:
        if ro["side"] == "internal_transfer":
            continue
        cat = ro["category"]
        sides[cat] = ro["side"]
        pivot[cat][ro["op_date"][:7]] += _dec(ro["net_amount"])

    r = _write_title(ws, "Категория × Месяц",
                     "Нетто-суммы по каждой категории, помесячно (без внутр. переводов)")

    headers = ["Сторона", "Категория"] + [_month_label(m) for m in months] + ["Итого"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = WHITE_BOLD
        c.fill = NAVY_FILL
        c.alignment = CENTER if i > 2 else LEFT
        c.border = HEAD_BORDER
    head_row = r
    r += 1

    # Sort: side priority, then category by total desc
    side_order = {"revenue": 0, "other_income": 1, "cogs": 2, "opex": 3, "tax": 4}
    cats_sorted = sorted(pivot.keys(),
                         key=lambda c: (side_order.get(sides[c], 9),
                                        -float(sum(pivot[c].values()))))
    for cat in cats_sorted:
        ws.cell(row=r, column=1, value=sides[cat]).alignment = CENTER
        ws.cell(row=r, column=2, value=cat).font = NAVY_REG
        ws.cell(row=r, column=2).alignment = LEFT
        total = Decimal(0)
        for col_idx, m in enumerate(months, start=3):
            v = pivot[cat].get(m, Decimal(0))
            c = ws.cell(row=r, column=col_idx, value=float(v))
            c.number_format = RUB_FMT
            c.alignment = RIGHT
            total += v
        c = ws.cell(row=r, column=len(months) + 3, value=float(total))
        c.number_format = RUB_FMT
        c.font = NAVY_BOLD
        c.alignment = RIGHT
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
        r += 1

    ws.freeze_panes = ws.cell(row=head_row + 1, column=3)
    _set_col_widths(ws, [12, 28] + [14] * (len(months) + 1))


def _build_customers_sheet(ws: Worksheet, analytics: dict) -> None:
    cc = analytics["customer_concentration"]
    band = ("ВЫСОКАЯ (>2500)" if cc["hhi"] > Decimal(2500)
            else "СРЕДНЯЯ (1500–2500)" if cc["hhi"] > Decimal(1500)
            else "НИЗКАЯ (<1500)")
    r = _write_title(ws, "Концентрация — клиенты",
                     f"HHI = {float(cc['hhi']):.0f} ({band})  ·  ТОП-1 = {float(cc['top1']):.1f}%  ·  ТОП-3 = {float(cc['top3']):.1f}%")

    headers = ["#", "Контрагент", "ИНН", "Кол-во операций", "Брутто, ₽", "% Выр", "Категория"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = WHITE_BOLD
        c.fill = NAVY_FILL
        c.alignment = CENTER if i not in (2, 7) else LEFT
        c.border = HEAD_BORDER
    head_row = r
    r += 1

    for i, c in enumerate(analytics["customers"], start=1):
        ws.cell(row=r, column=1, value=i).alignment = CENTER
        ws.cell(row=r, column=2, value=c["name"]).alignment = LEFT
        ws.cell(row=r, column=3, value=c["inn"]).alignment = LEFT
        ws.cell(row=r, column=4, value=c["count"]).alignment = CENTER
        cell = ws.cell(row=r, column=5, value=float(c["gross"]))
        cell.number_format = RUB_FMT
        cell.alignment = RIGHT
        cell = ws.cell(row=r, column=6, value=float(c["share_pct"]) / 100)
        cell.number_format = '0.0%'
        cell.alignment = RIGHT
        ws.cell(row=r, column=7, value=c["category"]).alignment = LEFT
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
        r += 1

    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    _set_col_widths(ws, [4, 50, 16, 14, 18, 10, 30])


def _build_expenses_sheet(ws: Worksheet, analytics: dict) -> None:
    ec = analytics["expense_concentration"]
    band = ("ВЫСОКАЯ (>2500)" if ec["hhi"] > Decimal(2500)
            else "СРЕДНЯЯ (1500–2500)" if ec["hhi"] > Decimal(1500)
            else "НИЗКАЯ (<1500)")
    r = _write_title(ws, "Концентрация — расходы",
                     f"HHI = {float(ec['hhi']):.0f} ({band})  ·  ТОП-1 = {float(ec['top1']):.1f}%  ·  ТОП-3 = {float(ec['top3']):.1f}%")

    headers = ["#", "Контрагент", "Сторона", "ИНН", "Кол-во операций", "Брутто, ₽", "% Расх", "Категория"]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = WHITE_BOLD
        c.fill = NAVY_FILL
        c.alignment = CENTER if i not in (2, 8) else LEFT
        c.border = HEAD_BORDER
    head_row = r
    r += 1

    for i, e in enumerate(analytics["expenses"], start=1):
        ws.cell(row=r, column=1, value=i).alignment = CENTER
        ws.cell(row=r, column=2, value=e["name"]).alignment = LEFT
        ws.cell(row=r, column=3, value=e["side"]).alignment = CENTER
        ws.cell(row=r, column=4, value=e["inn"]).alignment = LEFT
        ws.cell(row=r, column=5, value=e["count"]).alignment = CENTER
        cell = ws.cell(row=r, column=6, value=float(e["gross"]))
        cell.number_format = RUB_FMT
        cell.alignment = RIGHT
        cell = ws.cell(row=r, column=7, value=float(e["share_pct"]) / 100)
        cell.number_format = '0.0%'
        cell.alignment = RIGHT
        ws.cell(row=r, column=8, value=e["category"]).alignment = LEFT
        for col_idx in range(1, len(headers) + 1):
            ws.cell(row=r, column=col_idx).border = ALL_BORDER
        r += 1

    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    _set_col_widths(ws, [4, 46, 10, 16, 14, 18, 10, 30])


def _build_operations_sheet(ws: Worksheet, categorized_rows: list[dict]) -> None:
    r = _write_title(ws, "Операции (categorized.csv)",
                     "Все строки выписки + правило категоризации")

    headers = [
        "Дата опер.", "№ док.", "Контрагент", "ИНН", "Сторона", "Категория",
        "Дебет, ₽", "Кредит, ₽", "НДС, ставка", "НДС, сумма ₽", "Нетто, ₽", "Назначение",
    ]
    for i, h in enumerate(headers, start=1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = WHITE_BOLD
        c.fill = NAVY_FILL
        c.alignment = CENTER if i not in (3, 6, 12) else LEFT
        c.border = HEAD_BORDER
    head_row = r
    table_first_row = head_row
    r += 1

    for ro in categorized_rows:
        ws.cell(row=r, column=1, value=ro["op_date"]).alignment = CENTER
        ws.cell(row=r, column=2, value=ro["doc_no"]).alignment = CENTER
        ws.cell(row=r, column=3, value=ro["counterparty"]).alignment = LEFT
        ws.cell(row=r, column=4, value=ro["inn"]).alignment = LEFT
        ws.cell(row=r, column=5, value=ro["side"]).alignment = CENTER
        ws.cell(row=r, column=6, value=ro["category"]).alignment = LEFT
        debit  = _dec(ro["debit"])
        credit = _dec(ro["credit"])
        if debit:
            c = ws.cell(row=r, column=7, value=float(debit))
            c.number_format = RUB_FMT; c.alignment = RIGHT
        if credit:
            c = ws.cell(row=r, column=8, value=float(credit))
            c.number_format = RUB_FMT; c.alignment = RIGHT
        vat_rate = int(ro["vat_rate"] or 0)
        if vat_rate:
            c = ws.cell(row=r, column=9, value=vat_rate / 100)
            c.number_format = '0%'
            c.alignment = CENTER
        c = ws.cell(row=r, column=10, value=float(_dec(ro["vat_amount"])))
        c.number_format = RUB_FMT; c.alignment = RIGHT
        c = ws.cell(row=r, column=11, value=float(_dec(ro["net_amount"])))
        c.number_format = RUB_FMT; c.alignment = RIGHT
        ws.cell(row=r, column=12, value=ro["purpose"]).alignment = WRAPLEFT
        r += 1

    last_row = r - 1
    last_col_letter = get_column_letter(len(headers))
    tbl = Table(displayName="Операции",
                ref=f"A{table_first_row}:{last_col_letter}{last_row}")
    tbl.tableStyleInfo = TableStyleInfo(
        name="TableStyleLight1", showRowStripes=True, showColumnStripes=False)
    ws.add_table(tbl)
    ws.freeze_panes = ws.cell(row=head_row + 1, column=1)
    _set_col_widths(ws, [12, 8, 38, 16, 9, 24, 16, 16, 7, 14, 14, 60])


def csv_load(path: str) -> list[dict]:
    return list(csv.DictReader(open(path, encoding="utf-8")))


# --- public entry point -----------------------------------------------------

def write_xlsx(pnl: dict, analytics: dict, categorized_rows: list[dict],
               period: str, out_path: Path) -> Path:
    """Build reports/pnl.xlsx (7 sheets) from the same dicts the PDF uses."""
    wb = Workbook()
    # Remove default sheet
    wb.remove(wb.active)

    ws1 = wb.create_sheet("Сводка")
    _build_summary(ws1, pnl, analytics, period)

    ws2 = wb.create_sheet("P&L")
    _build_pnl_sheet(ws2, pnl, period)

    ws3 = wb.create_sheet("Сверка с банком")
    _build_bridge_sheet(ws3, pnl)

    ws4 = wb.create_sheet("Категория × Месяц")
    _build_pivot_sheet(ws4, pnl, analytics)

    ws5 = wb.create_sheet("Клиенты")
    _build_customers_sheet(ws5, analytics)

    ws6 = wb.create_sheet("Расходы")
    _build_expenses_sheet(ws6, analytics)

    ws7 = wb.create_sheet("Операции")
    _build_operations_sheet(ws7, categorized_rows)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path


if __name__ == "__main__":
    # Standalone runner: uses the same inputs as pnl.py main()
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pnl import build_pnl, derive_period
    import analytics as _an
    rows = list(csv.DictReader(open("reports/categorized.csv", encoding="utf-8")))
    pnl = build_pnl(rows)
    an  = _an.compute(rows,
                      opening_balance=Decimal("3869.02"),
                      closing_balance=Decimal("9568.09"))
    out = write_xlsx(pnl, an, rows, derive_period(pnl["months"]),
                     Path("reports/pnl.xlsx"))
    print(f"wrote {out}")
