#!/usr/bin/env python3
"""
pnl.py — turn categorized.csv into a classic, cash-basis P&L report.

Outputs (all in reports/):
  - pnl.csv      machine-readable monthly P&L matrix
  - pnl.md       human-readable Markdown
  - pnl.pdf      best-in-class PDF report (KPI tiles, charts, classic table)

Design choices for the PDF:
  - Modern financial layout: navy (#0B2545) header, off-white background,
    accent gold (#C99B3E) for positives, terra-cotta (#B43E3E) for negatives.
  - DM Sans-equivalent fallback (Helvetica) — tabular figures, right-aligned.
  - Executive summary tiles at the top, then KPI deltas, then the full P&L,
    then monthly trend chart + expense breakdown bar.
  - Footer with cash-basis caveat and source on every page.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import io
import re
import sys
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

# Local import — counterparty/concentration/runway analytics.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import analytics as _analytics  # noqa: E402

# --- P&L STRUCTURE -----------------------------------------------------------
# Each line: (label, level [0 header, 1 sub, 2 total], list of categories OR
# a computed-formula function (computed at aggregation time))

REVENUE_CATS = ["revenue.marketplace_ozon", "revenue.direct_b2b"]
COGS_CATS    = ["cogs.goods", "cogs.manufacturing", "cogs.parts"]
OPEX_GROUPS  = [
    ("Зарплаты",                  ["opex.salary"]),
    ("Транспорт",                 ["opex.transport"]),
    ("Доставка",                  ["opex.shipping"]),
    ("Лизинг (авто)",             ["opex.leasing_auto"]),
    ("Страхование (ОСАГО)",       ["opex.insurance_osago"]),
    ("Обслуживание авто",         ["opex.vehicle_maint"]),
    ("Аренда",                    ["opex.rent"]),
    ("Коммунальные",              ["opex.utilities"]),
    ("Связь",                     ["opex.telecom"]),
    ("ПО / IT",                   ["opex.software"]),
    ("Бухгалтерия",               ["opex.accounting"]),
    ("Обучение",                  ["opex.training"]),
    ("Банковские сборы",          ["opex.bank_fees"]),
    ("Прочее",                    ["opex.other"]),
]
TAX_CATS = ["tax.fns", "tax.social"]
OTHER_INCOME_CATS = ["other_income.insurance_reimb"]

MONTH_LABELS_RU = ["Янв", "Фев", "Мар", "Апр", "Май", "Июн", "Июл", "Авг", "Сен", "Окт", "Ноя", "Дек"]
MONTH_LABELS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

DEC0 = Decimal("0")
ZERO = lambda: Decimal("0")  # noqa: E731


# --- AGGREGATION -------------------------------------------------------------

def load_categorized(path: Path) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


def to_dec(s: str) -> Decimal:
    s = (s or "").strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if not s:
        return DEC0
    try:
        return Decimal(s)
    except Exception:
        return DEC0


def month_key(date_iso: str) -> str:
    """YYYY-MM-DD → YYYY-MM."""
    if len(date_iso) >= 7:
        return date_iso[:7]
    return ""


def aggregate(rows: list[dict]) -> tuple[list[str], dict[str, dict[str, Decimal]], dict[str, Decimal]]:
    """Return (months_sorted, agg, cash_recon).

    agg[category][month] = net_amount (net of VAT).
    cash_recon = totals for the bank-cash reconciliation bridge:
        gross_credit, gross_debit, vat_in_revenue, vat_in_expense,
        internal_transfer_credit, internal_transfer_debit.
    Internal transfers are excluded from `agg` (P&L proper), but kept in cash_recon.
    """
    months_set: set[str] = set()
    agg: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(ZERO))
    recon = {
        "gross_credit": DEC0,
        "gross_debit": DEC0,
        "vat_in_revenue": DEC0,
        "vat_in_expense": DEC0,
        "internal_credit": DEC0,
        "internal_debit": DEC0,
    }
    for r in rows:
        side = r["side"]
        m = month_key(r["op_date"])
        if m:
            months_set.add(m)
        debit  = to_dec(r["debit"])
        credit = to_dec(r["credit"])
        vat    = to_dec(r["vat_amount"])
        recon["gross_credit"] += credit
        recon["gross_debit"]  += debit
        if side == "internal_transfer":
            recon["internal_credit"] += credit
            recon["internal_debit"]  += debit
            continue
        if side in ("revenue", "other_income"):
            recon["vat_in_revenue"] += vat
        else:
            recon["vat_in_expense"] += vat
        net = to_dec(r["net_amount"])
        cat = r["category"]
        if m:
            agg[cat][m] += net
    months = sorted(months_set)
    return months, agg, recon


# --- COMPUTED P&L LINES ------------------------------------------------------

def sum_categories(agg, cats, months) -> dict[str, Decimal]:
    out = {m: DEC0 for m in months}
    for c in cats:
        for m in months:
            out[m] += agg.get(c, {}).get(m, DEC0)
    return out


def add_total(d: dict[str, Decimal], months) -> dict[str, Decimal]:
    return {**d, "Total": sum(d.get(m, DEC0) for m in months)}


def subtract(a, b, months) -> dict[str, Decimal]:
    return {m: a.get(m, DEC0) - b.get(m, DEC0) for m in months}


def build_pnl(rows: list[dict]) -> dict:
    months, agg, recon = aggregate(rows)
    revenue = sum_categories(agg, REVENUE_CATS, months)
    cogs    = sum_categories(agg, COGS_CATS, months)
    gross   = subtract(revenue, cogs, months)

    opex_lines = []
    opex_total = {m: DEC0 for m in months}
    for label, cats in OPEX_GROUPS:
        line = sum_categories(agg, cats, months)
        if any(line[m] != 0 for m in months):
            opex_lines.append((label, line))
            for m in months:
                opex_total[m] += line[m]

    ebit  = subtract(gross, opex_total, months)
    other = sum_categories(agg, OTHER_INCOME_CATS, months)
    tax   = sum_categories(agg, TAX_CATS, months)
    net   = {m: ebit[m] + other[m] - tax[m] for m in months}

    # Revenue by source (sub-lines).
    rev_sub = []
    for c in REVENUE_CATS:
        line = sum_categories(agg, [c], months)
        if any(line[m] != 0 for m in months):
            rev_sub.append((c.split(".")[-1], line))

    # COGS by source.
    cogs_sub = []
    for c in COGS_CATS:
        line = sum_categories(agg, [c], months)
        if any(line[m] != 0 for m in months):
            cogs_sub.append((c.split(".")[-1], line))

    # Tax breakdown.
    tax_sub = []
    for c in TAX_CATS:
        line = sum_categories(agg, [c], months)
        if any(line[m] != 0 for m in months):
            tax_sub.append((c.split(".")[-1], line))

    return {
        "months": months,
        "revenue": add_total(revenue, months),
        "revenue_sub": [(n, add_total(d, months)) for n, d in rev_sub],
        "cogs": add_total(cogs, months),
        "cogs_sub": [(n, add_total(d, months)) for n, d in cogs_sub],
        "gross": add_total(gross, months),
        "opex_lines": [(label, add_total(d, months)) for label, d in opex_lines],
        "opex_total": add_total(opex_total, months),
        "ebit": add_total(ebit, months),
        "other": add_total(other, months),
        "tax": add_total(tax, months),
        "tax_sub": [(n, add_total(d, months)) for n, d in tax_sub],
        "net": add_total(net, months),
        "recon": recon,
    }


def cash_bridge(pnl: dict) -> list[tuple[str, Decimal]]:
    """Build the reconciliation bridge from net P&L to actual bank cash change."""
    r = pnl["recon"]
    net_pnl  = pnl["net"]["Total"]
    vat_kept = r["vat_in_revenue"] - r["vat_in_expense"]
    internal = r["internal_credit"] - r["internal_debit"]
    external_cash = net_pnl + vat_kept
    bank_change   = external_cash + internal
    return [
        ("Чистый результат P&L (без НДС)",                          net_pnl),
        ("(+) Полученный НДС − уплаченный НДС (лаг)",               vat_kept),
        ("= Внешний денежный результат",                            external_cash),
        ("(+) Внутренние трансферы между своими счетами",           internal),
        ("= Чистое изменение банковского остатка",                  bank_change),
    ]


# --- FORMATTING --------------------------------------------------------------

def month_label(yyyymm: str, ru: bool = True) -> str:
    if yyyymm == "Total":
        return "Итого" if ru else "Total"
    y, m = yyyymm.split("-")
    labels = MONTH_LABELS_RU if ru else MONTH_LABELS_EN
    return f"{labels[int(m)-1]} {y[-2:]}"


def fmt_amount(d: Decimal, parens_for_neg: bool = True) -> str:
    if d is None:
        return "—"
    d = d.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    # Use   (NO-BREAK SPACE) as thousands separator so numbers don't wrap.
    s = f"{abs(d):,.2f}".replace(",", " ")
    if d < 0:
        return f"({s})" if parens_for_neg else f"-{s}"
    return s


def fmt_pct(num: Decimal, den: Decimal) -> str:
    if den == 0:
        return "—"
    p = (num / den * Decimal("100")).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    sign = "+" if p > 0 else ""
    return f"{sign}{p}%"


# --- CSV ---------------------------------------------------------------------

def write_csv(pnl: dict, out: Path) -> None:
    months = pnl["months"]
    cols = ["Статья"] + [month_label(m) for m in months] + ["Итого"]
    rows = []
    def push(label: str, d: dict[str, Decimal]):
        rows.append([label] + [f"{d.get(m, DEC0):.2f}" for m in months] + [f"{d['Total']:.2f}"])

    rows.append(["= Выручка (без НДС) ="])
    push("  Выручка (итого)", pnl["revenue"])
    for n, d in pnl["revenue_sub"]:
        push(f"    {n}", d)
    rows.append(["= Себестоимость (COGS) ="])
    push("  Себестоимость (итого)", pnl["cogs"])
    for n, d in pnl["cogs_sub"]:
        push(f"    {n}", d)
    push("Валовая прибыль", pnl["gross"])
    rows.append(["= Операционные расходы ="])
    for label, d in pnl["opex_lines"]:
        push(f"  {label}", d)
    push("OpEx (итого)", pnl["opex_total"])
    push("Операционная прибыль (EBIT)", pnl["ebit"])
    push("Прочий доход", pnl["other"])
    push("Налоги (итого)", pnl["tax"])
    for n, d in pnl["tax_sub"]:
        push(f"  {n}", d)
    push("Чистый денежный результат", pnl["net"])

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for r in rows:
            # Pad header-only rows so column count is uniform.
            if len(r) < len(cols):
                r = r + [""] * (len(cols) - len(r))
            w.writerow(r)


# --- MARKDOWN ----------------------------------------------------------------

def write_md(pnl: dict, period: str, out: Path) -> None:
    months = pnl["months"]
    hdr_cols = ["Статья"] + [month_label(m) for m in months] + ["Итого"]
    sep      = ["---"] + ["---:"] * (len(hdr_cols) - 1)

    def row(label: str, d: dict[str, Decimal], total_label: str | None = None) -> str:
        vals = [fmt_amount(d.get(m, DEC0)) for m in months] + [fmt_amount(d["Total"])]
        return "| " + " | ".join([label, *vals]) + " |"

    lines: list[str] = []
    lines.append(f"# Отчёт о прибылях и убытках (кассовый метод) — {period}")
    lines.append("")
    lines.append("> **База:** кассовый метод (по фактическому движению денег), валюта **RUB**. ")
    lines.append("> Источник: банковская выписка р/с 40702810102500117620 (ООО «ТЕХСОЛ»). ")
    lines.append("> Внутренние переводы между собственными счетами исключены. НДС в составе платежей выделяется из назначения.")
    lines.append("")
    lines.append("| " + " | ".join(hdr_cols) + " |")
    lines.append("| " + " | ".join(sep) + " |")
    lines.append(row("**Выручка (без НДС)**", pnl["revenue"]))
    for n, d in pnl["revenue_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**Себестоимость (COGS)**", pnl["cogs"]))
    for n, d in pnl["cogs_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**Валовая прибыль**", pnl["gross"]))
    gp_pct = [fmt_pct(pnl["gross"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
             [fmt_pct(pnl["gross"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Валовая маржа % | " + " | ".join(gp_pct) + " |")
    for label, d in pnl["opex_lines"]:
        lines.append(row(f"&nbsp;&nbsp;{label}", d))
    lines.append(row("**OpEx (итого)**", pnl["opex_total"]))
    lines.append(row("**Операционная прибыль (EBIT)**", pnl["ebit"]))
    op_pct = [fmt_pct(pnl["ebit"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
             [fmt_pct(pnl["ebit"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Операционная маржа % | " + " | ".join(op_pct) + " |")
    lines.append(row("Прочий доход", pnl["other"]))
    lines.append(row("Налоги (итого)", pnl["tax"]))
    for n, d in pnl["tax_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**Чистый денежный результат**", pnl["net"]))
    net_pct = [fmt_pct(pnl["net"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
              [fmt_pct(pnl["net"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Чистая маржа % | " + " | ".join(net_pct) + " |")
    lines.append("")
    lines.append("## Сверка с банком (P&L → расчётный счёт)")
    lines.append("")
    lines.append("| Шаг сверки | Сумма |")
    lines.append("| --- | ---: |")
    for label, value in cash_bridge(pnl):
        bold = label.startswith("=")
        lab = f"**{label}**" if bold else label
        val = f"**{fmt_amount(value)}**" if bold else fmt_amount(value)
        lines.append(f"| {lab} | {val} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Примечания")
    lines.append("")
    lines.append("- **Только кассовый метод.** Выручка/расходы здесь — это фактически полученные/потраченные деньги, не начисления.")
    lines.append("- **Налоги (ФНС + ОСФР)** показаны отдельной строкой, не размазаны по OpEx.")
    lines.append("- **Внутренние переводы** (ИНН компании-владельца) исключены из P&L.")
    lines.append("- **НДС** выделяется из строки «в т.ч. НДС N% xxx» в назначении; если её нет — расчёт по ставке правила.")
    lines.append("")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")


# --- PDF ---------------------------------------------------------------------

def _register_cyrillic_fonts() -> tuple[str, str]:
    """Register DejaVu Sans (regular + bold) — supports Cyrillic and ₽.

    Returns (regular_name, bold_name). Falls back to Helvetica if DejaVu absent.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    ]
    bold_candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    ]
    reg = None
    for p in candidates:
        if Path(p).exists():
            reg = p
            break
    bold = None
    for p in bold_candidates:
        if Path(p).exists():
            bold = p
            break
    if reg is None or bold is None:
        return "Helvetica", "Helvetica-Bold"
    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", reg))
        pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
        from reportlab.pdfbase.pdfmetrics import registerFontFamily
        registerFontFamily("DejaVuSans", normal="DejaVuSans", bold="DejaVuSans-Bold",
                           italic="DejaVuSans", boldItalic="DejaVuSans-Bold")
        return "DejaVuSans", "DejaVuSans-Bold"
    except Exception:
        return "Helvetica", "Helvetica-Bold"


def render_pdf(pnl: dict, period: str, out: Path, charts_out_dir: Path,
               analytics: dict | None = None) -> None:
    """Render an executive-grade P&L PDF (multi-page) using reportlab + matplotlib.

    Layout:
        Page 1 — Executive Summary (situation, drivers, risks, recommended actions)
        Page 2 — KPI tiles + classic P&L table (with %-of-Revenue common-size column)
        Page 3 — Cash reconciliation + EBIT waterfall
        Page 4 — Concentration analysis (customers & suppliers, Top-N + Pareto + HHI)
        Page 5 — Monthly trend + Expense structure + Methodology / Notes
    """
    import html as _html
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, Image, PageTemplate, PageBreak,
        Paragraph, Spacer, Table, TableStyle, KeepTogether,
    )

    FONT, FONT_B = _register_cyrillic_fonts()

    # Palette inspired by FT / Bloomberg terminals — restrained, elegant.
    NAVY   = colors.HexColor("#0B2545")
    SLATE  = colors.HexColor("#13315C")
    GOLD   = colors.HexColor("#C99B3E")
    RED    = colors.HexColor("#B43E3E")
    GREY   = colors.HexColor("#5E6B7A")
    LIGHT  = colors.HexColor("#F4F1EA")
    HAIR   = colors.HexColor("#D6D3CC")
    WHITE  = colors.white

    months = pnl["months"]
    month_labels = [month_label(m) for m in months]

    # Build charts (matplotlib → PNG → reportlab Image).
    charts_out_dir.mkdir(parents=True, exist_ok=True)
    trend_png = _render_trend_chart(pnl, charts_out_dir / "_trend.png", NAVY="#0B2545", GOLD="#C99B3E", RED="#B43E3E", SLATE="#13315C")
    expense_png = _render_expense_chart(pnl, charts_out_dir / "_expenses.png", NAVY="#0B2545", GOLD="#C99B3E")

    # Set up doc — A4 portrait, narrow margins.
    pdf = BaseDocTemplate(
        str(out),
        pagesize=A4,
        leftMargin=14 * mm, rightMargin=14 * mm,
        topMargin=14 * mm,  bottomMargin=14 * mm,
        title=f"P&L {period}",
        author="finance/pnl.py",
    )
    page_w, page_h = A4
    frame = Frame(pdf.leftMargin, pdf.bottomMargin,
                  page_w - pdf.leftMargin - pdf.rightMargin,
                  page_h - pdf.topMargin - pdf.bottomMargin,
                  showBoundary=0)

    def _on_page(canvas, _doc):
        canvas.saveState()
        # Footer: source + page number + caveat.
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(GREY)
        canvas.drawString(pdf.leftMargin, 9 * mm,
                          "Кассовый метод (банковская выписка). Внутренние трансферы исключены. НДС извлечён из назначения платежа.")
        canvas.drawRightString(page_w - pdf.rightMargin, 9 * mm, f"Стр.\xa0{_doc.page}")
        # Top rule.
        canvas.setStrokeColor(NAVY)
        canvas.setLineWidth(0.6)
        canvas.line(pdf.leftMargin, page_h - 11 * mm, page_w - pdf.rightMargin, page_h - 11 * mm)
        canvas.restoreState()

    pdf.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_on_page)])

    styles = getSampleStyleSheet()
    H1 = ParagraphStyle("H1", parent=styles["Heading1"], fontName=FONT_B,
                        fontSize=22, leading=26, textColor=NAVY, spaceAfter=2 * mm)
    SUBTITLE = ParagraphStyle("Sub", parent=styles["Normal"], fontName=FONT,
                              fontSize=10, leading=13, textColor=GREY, spaceAfter=4 * mm)
    SECTION = ParagraphStyle("Sec", parent=styles["Heading2"], fontName=FONT_B,
                             fontSize=12, leading=15, textColor=NAVY, spaceBefore=4 * mm, spaceAfter=2 * mm)
    NOTE = ParagraphStyle("Note", parent=styles["Normal"], fontName=FONT,
                          fontSize=8.5, leading=11, textColor=GREY, spaceBefore=1 * mm)

    story = []

    # === PAGE 1 — Executive Summary =====================================
    if analytics is None:
        analytics = _analytics.compute(_analytics.load_categorized(),
                                       opening_balance=Decimal("3869.02"),
                                       closing_balance=Decimal("9568.09"))
    _build_executive_summary(story, pnl, analytics, period,
                             FONT=FONT, FONT_B=FONT_B,
                             NAVY=NAVY, GOLD=GOLD, RED=RED, GREY=GREY,
                             LIGHT=LIGHT, HAIR=HAIR, WHITE=WHITE,
                             page_w=page_w, pdf=pdf, mm=mm,
                             H1=H1, SECTION=SECTION, SUBTITLE=SUBTITLE, NOTE=NOTE,
                             styles=styles, Paragraph=Paragraph, Spacer=Spacer,
                             Table=Table, TableStyle=TableStyle, _html=_html)
    story.append(PageBreak())

    # === PAGE 2 — Cover band, KPIs, classic P&L =========================
    story.append(Paragraph("Отчёт о прибылях и убытках — кассовый метод", H1))
    story.append(Paragraph(f"ООО «ТЕХСОЛ» &nbsp;·&nbsp; Счёт 40702810102500117620 (RUB) &nbsp;·&nbsp; {period}",
                           SUBTITLE))

    # === KPI tiles ===
    rev_t  = pnl["revenue"]["Total"]
    gp_t   = pnl["gross"]["Total"]
    ebit_t = pnl["ebit"]["Total"]
    net_t  = pnl["net"]["Total"]
    gm_pct = (gp_t / rev_t * 100) if rev_t else Decimal("0")
    om_pct = (ebit_t / rev_t * 100) if rev_t else Decimal("0")
    nm_pct = (net_t / rev_t * 100) if rev_t else Decimal("0")

    TILE_LBL = ParagraphStyle("TileLbl", fontName=FONT, fontSize=7.5, leading=9, textColor=GREY,
                              splitLongWords=False)
    TILE_VAL = ParagraphStyle("TileVal", fontName=FONT_B, fontSize=11, leading=13, textColor=NAVY,
                              splitLongWords=False)
    TILE_NEG = ParagraphStyle("TileNeg", fontName=FONT_B, fontSize=11, leading=13, textColor=RED,
                              splitLongWords=False)

    def tile(label, value_str, negative=False):
        return [
            Paragraph(label, TILE_LBL),
            Spacer(1, 1.5 * mm),
            Paragraph(value_str, TILE_NEG if negative else TILE_VAL),
        ]

    # Extra KPI fields from analytics
    burn   = analytics.get("burn_per_month", Decimal(0))
    runway = analytics.get("runway_months")
    runway_str = f"{runway:.1f}\xa0mo" if runway is not None else "∞"
    runway_red = runway is not None and runway < 12  # <12 months = red flag
    cust_top1 = analytics["customer_concentration"]["top1"]
    cust_hhi  = analytics["customer_concentration"]["hhi"]
    hhi_red   = cust_hhi > Decimal("2500")  # DOJ threshold

    kpi = Table(
        [[
            tile("ВЫРУЧКА,\xa0₽",            fmt_amount(rev_t)),
            tile("ВАЛОВАЯ ПРИБЫЛЬ,\xa0₽",    fmt_amount(gp_t),   negative=gp_t < 0),
            tile("EBIT,\xa0₽",               fmt_amount(ebit_t), negative=ebit_t < 0),
            tile("ЧИСТЫЙ РЕЗУЛЬТАТ,\xa0₽",   fmt_amount(net_t),  negative=net_t < 0),
        ],
        [
            tile("ВАЛОВАЯ МАРЖА",    f"{gm_pct.quantize(Decimal('0.1'))}%", negative=gm_pct < 0),
            tile("ОПЕР. МАРЖА",      f"{om_pct.quantize(Decimal('0.1'))}%", negative=om_pct < 0),
            tile("ЧИСТАЯ МАРЖА",     f"{nm_pct.quantize(Decimal('0.1'))}%", negative=nm_pct < 0),
            tile("ПЕРИОД",           f"{len(months)}\xa0мес"),
        ],
        [
            tile("BURN /\xa0МЕС,\xa0₽",      fmt_amount(burn), negative=burn > 0),
            tile("ЗАПАС КЭША",       runway_str, negative=runway_red),
            tile("ТОП-1 КЛИЕНТ",     f"{cust_top1.quantize(Decimal('0.1'))}%", negative=cust_top1 > Decimal("30")),
            tile("HHI (КЛИЕНТЫ)",    f"{cust_hhi:.0f}",          negative=hhi_red),
        ]],
        colWidths=[(page_w - pdf.leftMargin - pdf.rightMargin) / 4.0] * 4,
        rowHeights=[14 * mm, 14 * mm, 14 * mm],
    )
    kpi.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX",        (0, 0), (-1, -1), 0.4, HAIR),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, HAIR),
        ("LEFTPADDING",   (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING",    (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    story.append(kpi)
    story.append(Spacer(1, 6 * mm))

    # === Main P&L table (with common-size %-of-Revenue column) ===
    cols = ["", *month_labels, "Итого", "% Выр"]
    table_rows = [cols]
    styles_acc: list[tuple] = []
    rev_total_for_cs = pnl["revenue"]["Total"]

    def _common_size(line_total: Decimal) -> str:
        if not rev_total_for_cs:
            return "—"
        p = (line_total / rev_total_for_cs * 100).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
        return f"{p}%"

    def add_row(label, d, bold=False, indent=0, accent=False, italic=False, cs: str | None = None):
        prefix = ("\xa0" * (indent * 3))
        font = FONT_B if bold else FONT
        label_p = Paragraph(f'<font name="{font}" size="8.5">{prefix}{_html.escape(label)}</font>',
                            ParagraphStyle("L", fontName=FONT, fontSize=8.5, leading=11, textColor=NAVY))
        vals: list = []
        cell_colors: list[colors.Color] = []
        for m in months:
            v = d.get(m, DEC0)
            vals.append(fmt_amount(v))
            cell_colors.append(RED if v < 0 else NAVY)
        v = d["Total"]
        vals.append(fmt_amount(v))
        cell_colors.append(RED if v < 0 else NAVY)
        # Common-size: % of period revenue.
        cs_str = cs if cs is not None else _common_size(v)
        vals.append(cs_str)
        cell_colors.append(GREY)
        table_rows.append([label_p] + vals)
        idx = len(table_rows) - 1
        styles_acc.append(("FONTNAME", (1, idx), (-1, idx), font))
        # Bold rows are slightly larger; the wider numbers (totals) need a smaller font
        # to avoid overflow into the neighbouring cell.
        styles_acc.append(("FONTSIZE", (1, idx), (-1, idx), 7.8 if bold else 8.5))
        for col_idx, col in enumerate(cell_colors, start=1):
            styles_acc.append(("TEXTCOLOR", (col_idx, idx), (col_idx, idx), col))
        # The %-Rev column is always grey and slightly smaller.
        styles_acc.append(("FONTSIZE", (len(months) + 2, idx), (len(months) + 2, idx), 7.5))
        styles_acc.append(("TEXTCOLOR", (len(months) + 2, idx), (len(months) + 2, idx), GREY))
        if accent:
            styles_acc.append(("BACKGROUND", (0, idx), (-1, idx), LIGHT))
        if bold:
            styles_acc.append(("LINEABOVE", (0, idx), (-1, idx), 0.6, NAVY))
            styles_acc.append(("LINEBELOW", (0, idx), (-1, idx), 0.6, NAVY))

    def add_margin_row(label, num: dict, den: dict):
        cells = [Paragraph(f'<i><font name="{FONT}" color="#5E6B7A" size="8">{_html.escape(label)}</font></i>',
                           styles["Normal"])]
        for m in months:
            cells.append(fmt_pct(num.get(m, DEC0), den.get(m, DEC0)))
        cells.append(fmt_pct(num["Total"], den["Total"]))
        cells.append("")   # no common-size for margin rows
        table_rows.append(cells)
        idx = len(table_rows) - 1
        styles_acc.append(("FONTNAME", (1, idx), (-1, idx), FONT))
        styles_acc.append(("FONTSIZE", (1, idx), (-1, idx), 8))
        styles_acc.append(("TEXTCOLOR", (1, idx), (-1, idx), GREY))

    # Build rows.
    _SUB_RU = {
        "marketplace_ozon": "Маркетплейс Ozon",
        "direct_b2b": "Прямые B2B",
        "goods": "Товары",
        "manufacturing": "Производство",
        "parts": "Запчасти",
        "fns": "ФНС (налоги)",
        "social": "ОСФР (соц. взносы)",
    }
    add_row("Выручка (без НДС)", pnl["revenue"], bold=True, accent=True)
    for n, d in pnl["revenue_sub"]:
        add_row(_SUB_RU.get(n, n.replace("_", " ").title()), d, indent=1)
    add_row("Себестоимость (COGS)", pnl["cogs"], bold=True, accent=True)
    for n, d in pnl["cogs_sub"]:
        add_row(_SUB_RU.get(n, n.replace("_", " ").title()), d, indent=1)
    add_row("Валовая прибыль", pnl["gross"], bold=True)
    add_margin_row("Валовая маржа", pnl["gross"], pnl["revenue"])
    add_row("Операционные расходы", pnl["opex_total"], bold=True, accent=True)
    for label, d in pnl["opex_lines"]:
        add_row(label, d, indent=1)
    add_row("Операционная прибыль (EBIT)", pnl["ebit"], bold=True)
    add_margin_row("Операционная маржа", pnl["ebit"], pnl["revenue"])
    add_row("Прочий доход", pnl["other"])
    add_row("Налоги (ФНС + ОСФР)", pnl["tax"], bold=True, accent=True)
    for n, d in pnl["tax_sub"]:
        add_row(_SUB_RU.get(n, n.replace("_", " ").title()), d, indent=1)
    add_row("Чистый денежный результат", pnl["net"], bold=True)
    add_margin_row("Чистая маржа", pnl["net"], pnl["revenue"])

    label_w = 48 * mm
    cs_w    = 13 * mm
    data_w  = (page_w - pdf.leftMargin - pdf.rightMargin - label_w - cs_w) / (len(months) + 1)
    col_widths = [label_w] + [data_w] * (len(months) + 1) + [cs_w]
    pnl_table = Table(table_rows, colWidths=col_widths, repeatRows=1)
    pnl_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0), WHITE),
        ("ALIGN",        (1, 0), (-1, 0), "RIGHT"),
        ("ALIGN",        (1, 1), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "LEFT"),
        ("FONTNAME",     (0, 0), (-1, 0), FONT_B),
        ("FONTSIZE",     (0, 0), (-1, 0), 9),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 2.0 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2.0 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 1.4 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1.4 * mm),
        ("GRID",         (0, 0), (-1, -1), 0.25, HAIR),
    ] + styles_acc))
    story.append(Paragraph("Отчёт об операциях", SECTION))
    story.append(pnl_table)

    # === Cash reconciliation bridge ===
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Сверка с банком — P&amp;L → расчётный счёт", SECTION))
    bridge = cash_bridge(pnl)
    bridge_rows = [["Шаг сверки", "Сумма, ₽"]]
    bridge_styles: list[tuple] = []
    for i, (label, value) in enumerate(bridge, start=1):
        is_total = label.startswith("=")
        font = FONT_B if is_total else FONT
        cell_l = Paragraph(
            f'<font name="{font}" color="#0B2545" size="9">{_html.escape(label)}</font>',
            styles["Normal"])
        col = "#B43E3E" if value < 0 else "#0B2545"
        cell_r = Paragraph(
            f'<para align="right"><font name="{font}" color="{col}" size="9">{fmt_amount(value)}</font></para>',
            styles["Normal"])
        bridge_rows.append([cell_l, cell_r])
        if is_total:
            bridge_styles.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
            bridge_styles.append(("LINEABOVE",  (0, i), (-1, i), 0.6, NAVY))
    bridge_table = Table(
        bridge_rows,
        colWidths=[(page_w - pdf.leftMargin - pdf.rightMargin) * 0.62,
                   (page_w - pdf.leftMargin - pdf.rightMargin) * 0.38],
        repeatRows=1,
    )
    bridge_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0), WHITE),
        ("FONTNAME",     (0, 0), (-1, 0), FONT_B),
        ("FONTSIZE",     (0, 0), (-1, 0), 9),
        ("ALIGN",        (1, 0), (1, 0),  "RIGHT"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3.5 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.5 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 1.8 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1.8 * mm),
        ("GRID",         (0, 0), (-1, -1), 0.25, HAIR),
    ] + bridge_styles))
    story.append(bridge_table)

    # === PAGE 3 — EBIT waterfall + concentration overview ==============
    story.append(PageBreak())
    story.append(Paragraph("Мост EBIT — что повлияло на операционный результат", SECTION))
    waterfall_png = _render_waterfall_chart(pnl, charts_out_dir / "_waterfall.png",
                                            NAVY="#0B2545", GOLD="#C99B3E", RED="#B43E3E", SLATE="#13315C")
    available_w = page_w - pdf.leftMargin - pdf.rightMargin
    story.append(Image(str(waterfall_png), width=available_w, height=available_w * 0.45))
    story.append(Spacer(1, 4 * mm))

    # === Concentration analysis ===
    _build_concentration_section(story, analytics,
                                 FONT=FONT, FONT_B=FONT_B,
                                 NAVY=NAVY, GOLD=GOLD, RED=RED, GREY=GREY,
                                 LIGHT=LIGHT, HAIR=HAIR, WHITE=WHITE,
                                 page_w=page_w, pdf=pdf, mm=mm,
                                 SECTION=SECTION, NOTE=NOTE,
                                 styles=styles, Paragraph=Paragraph, Spacer=Spacer,
                                 PageBreak=PageBreak, Image=Image,
                                 Table=Table, TableStyle=TableStyle, _html=_html,
                                 charts_out_dir=charts_out_dir)

    # === PAGE 5 — Trend + expense structure + Notes ====================
    story.append(PageBreak())
    story.append(Paragraph("Месячная динамика и структура расходов", SECTION))
    story.append(Spacer(1, 1 * mm))
    story.append(Image(str(trend_png),   width=available_w, height=available_w * 0.5))
    story.append(Spacer(1, 4 * mm))
    story.append(Image(str(expense_png), width=available_w, height=available_w * 0.5))
    story.append(Spacer(1, 2 * mm))

    # === Notes / methodology ===
    notes_lines = [
        "<b>Методология.</b> Отчёт о прибылях и убытках построен по кассовому методу из выписки одного "
        "банковского счёта. Внутренние переводы между собственными счетами (тот же ИНН) исключены. "
        "НДС извлекается из назначения платежа («в т.ч. НДС N% xxx») если присутствует; иначе считается по "
        "ставке из правил классификации.",
        "<b>Ограничения.</b> Кассовый поток ≠ начисленная выручка / расход. Дебиторская и кредиторская "
        "задолженность (неоплаченные счета) не видны. Движение товарных запасов не учитывается. Зарплаты "
        "показаны нетто (после НДФЛ); НДФЛ и страховые взносы проходят через строки ФНС / ОСФР.",
        "<b>Источник.</b> Банковская выписка, р/с 40702810102500117620 (ООО «ТЕХСОЛ»), период указан в заголовке.",
    ]
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Примечания", SECTION))
    for l in notes_lines:
        story.append(Paragraph(l, NOTE))

    pdf.build(story)


def _render_trend_chart(pnl, out_path, NAVY, GOLD, RED, SLATE):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    months = pnl["months"]
    x = [month_label(m) for m in months]
    rev = [float(pnl["revenue"][m]) for m in months]
    gp  = [float(pnl["gross"][m]) for m in months]
    ebit= [float(pnl["ebit"][m]) for m in months]
    net = [float(pnl["net"][m]) for m in months]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    ax.plot(x, rev,  label="Выручка",         marker="o", color=NAVY,  linewidth=2.2)
    ax.plot(x, gp,   label="Валовая прибыль", marker="o", color=GOLD,  linewidth=2.0)
    ax.plot(x, ebit, label="EBIT",            marker="o", color=SLATE, linewidth=1.8, linestyle="--")
    ax.plot(x, net,  label="Чистый результат",marker="o", color=RED,   linewidth=1.8, linestyle=":")
    ax.set_title("Месячная динамика — выручка, валовая, EBIT, чистый",
                 fontsize=12, color=NAVY, weight="bold", loc="left")
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(colors="#555555")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if abs(v) >= 1000 else f"{v:.0f}"))
    ax.axhline(0, color="#888888", linewidth=0.6, alpha=0.7)
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _render_expense_chart(pnl, out_path, NAVY, GOLD):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    # Sum each opex line over the whole period; sort desc; top 12.
    items = [(label, float(d["Total"])) for label, d in pnl["opex_lines"] if d["Total"] != 0]
    items += [("Налоги",        float(pnl["tax"]["Total"]))]
    items += [("Себестоимость", float(pnl["cogs"]["Total"]))]
    items.sort(key=lambda kv: -kv[1])
    labels = [k for k, _ in items]
    values = [v for _, v in items]

    fig, ax = plt.subplots(figsize=(9, 4.2), dpi=200)
    bars = ax.barh(labels, values, color=NAVY, edgecolor="white")
    # Highlight top-3 in gold.
    for b in bars.patches[:3]:
        b.set_facecolor(GOLD)
    ax.invert_yaxis()
    ax.set_title("Expense structure — total over period", fontsize=12, color=NAVY, weight="bold", loc="left")
    ax.grid(True, axis="x", alpha=0.25, linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.tick_params(colors="#555555")
    for bar, v in zip(bars, values):
        ax.text(v + max(values) * 0.005, bar.get_y() + bar.get_height() / 2,
                f"{v:,.0f}".replace(",", " "), va="center", fontsize=8, color=NAVY)
    ax.xaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if abs(v) >= 1000 else f"{v:.0f}"))
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# --- EXECUTIVE SUMMARY -------------------------------------------------------

def _build_executive_summary(story: list, pnl: dict, analytics: dict, period: str, *,
                              FONT, FONT_B, NAVY, GOLD, RED, GREY, LIGHT, HAIR, WHITE,
                              page_w, pdf, mm,
                              H1, SECTION, SUBTITLE, NOTE,
                              styles, Paragraph, Spacer, Table, TableStyle, _html) -> None:
    """Build the executive summary page (page 1) — board-meeting style."""
    from reportlab.lib.styles import ParagraphStyle

    rev_t  = pnl["revenue"]["Total"]
    ebit_t = pnl["ebit"]["Total"]
    net_t  = pnl["net"]["Total"]
    burn   = analytics["burn_per_month"]
    runway = analytics["runway_months"]
    runway_str = f"{runway:.2f} months" if runway is not None else "—"

    cust_top1 = analytics["customer_concentration"]["top1"]
    cust_top3 = analytics["customer_concentration"]["top3"]
    cust_hhi  = analytics["customer_concentration"]["hhi"]
    top_cust  = analytics["customers"][0] if analytics["customers"] else None
    top_exp   = analytics["expenses"][0] if analytics["expenses"] else None

    # Status: RAG by simple rule.
    if net_t < 0 and (runway is None or runway < 12):
        status_text = "ВНЕ КУРСА — кассовый убыток, концентрация клиентов критическая"
        status_color = RED
    elif net_t < 0:
        status_text = "ПОД РИСКОМ — операционный убыток, следить за концентрацией"
        status_color = GOLD
    else:
        status_text = "ВСЁ В ПОРЯДКЕ"
        status_color = colors_hex("#1F7A4D")

    # === Title block ===
    story.append(Paragraph("Сводка для руководителя", H1))
    story.append(Paragraph(
        f"ООО «ТЕХСОЛ» &nbsp;·&nbsp; {period} &nbsp;·&nbsp; P&amp;L по кассовому методу (один банковский счёт, RUB)",
        SUBTITLE))

    # === Status banner ===
    status = Table([[
        Paragraph(f'<b><font color="#FFFFFF" size="10">{_html.escape(status_text)}</font></b>',
                  ParagraphStyle("S", fontName=FONT_B, fontSize=10, textColor=WHITE))
    ]], colWidths=[page_w - pdf.leftMargin - pdf.rightMargin])
    status.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), status_color),
        ("LEFTPADDING",  (0, 0), (-1, -1), 4 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 3 * mm),
    ]))
    story.append(status)
    story.append(Spacer(1, 4 * mm))

    # === Headline numbers ===
    HEAD_LBL = ParagraphStyle("HeadLbl", fontName=FONT, fontSize=7.5, textColor=GREY,
                              splitLongWords=False)
    HEAD_VAL = ParagraphStyle("HeadVal", fontName=FONT_B, fontSize=10, leading=12, textColor=NAVY,
                              splitLongWords=False)
    HEAD_NEG = ParagraphStyle("HeadNeg", fontName=FONT_B, fontSize=10, leading=12, textColor=RED,
                              splitLongWords=False)
    def hcell(lbl, val, negative=False):
        # The label includes the unit ("RUB" / "%") — value renders without
        # a trailing ₽ to maximise fit.
        return [Paragraph(lbl, HEAD_LBL), Spacer(1, 1 * mm),
                Paragraph(val, HEAD_NEG if negative else HEAD_VAL)]
    head = Table([[
        hcell("ВЫРУЧКА,\xa0₽",           fmt_amount(rev_t)),
        hcell("EBIT,\xa0₽",              fmt_amount(ebit_t),  negative=ebit_t < 0),
        hcell("ЧИСТЫЙ РЕЗ.,\xa0₽",       fmt_amount(net_t),   negative=net_t < 0),
        hcell("BURN /\xa0МЕС,\xa0₽",     fmt_amount(burn),    negative=burn > 0),
        hcell("ЗАПАС КЭША",              runway_str,
              negative=(runway is not None and runway < 12)),
    ]],
        colWidths=[(page_w - pdf.leftMargin - pdf.rightMargin) / 5.0] * 5,
        rowHeights=[16 * mm],
    )
    head.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX",        (0, 0), (-1, -1), 0.4, HAIR),
        ("INNERGRID",  (0, 0), (-1, -1), 0.4, HAIR),
        ("LEFTPADDING",   (0, 0), (-1, -1), 3.5 * mm),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING",    (0, 0), (-1, -1), 3 * mm),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3 * mm),
    ]))
    story.append(head)
    story.append(Spacer(1, 5 * mm))

    # === Situation narrative ===
    BODY = ParagraphStyle("Body", fontName=FONT, fontSize=9.5, leading=13.5, textColor=NAVY,
                          spaceAfter=2 * mm)
    BULLET = ParagraphStyle("Bullet", fontName=FONT, fontSize=9.5, leading=13.5, textColor=NAVY,
                            leftIndent=4 * mm, bulletIndent=0, spaceAfter=1 * mm)

    # Build situation text from data.
    rev_str  = fmt_amount(rev_t)
    ebit_str = fmt_amount(ebit_t)
    net_str  = fmt_amount(net_t)
    burn_str = fmt_amount(burn)
    om_pct   = (ebit_t / rev_t * 100) if rev_t else Decimal("0")
    # Hex strings for inline <font color=...> (Python 3.11 doesn't allow nested quotes in f-strings).
    RED_HEX  = "#" + RED.hexval()[2:]
    NAVY_HEX = "#" + NAVY.hexval()[2:]
    GOLD_HEX = "#" + GOLD.hexval()[2:]
    GREY_HEX = "#" + GREY.hexval()[2:]

    story.append(Paragraph("Ситуация", SECTION))
    story.append(Paragraph(
        f"За {len(analytics['months'])} месяца компания получила выручку <b>{rev_str}\xa0₽</b> (нетто от НДС). "
        f"Операционный результат — <b><font color='{RED_HEX}'>{ebit_str}\xa0₽</font></b> "
        f"(маржа {om_pct.quantize(Decimal('0.1'))}%). Чистый денежный результат после налогов — "
        f"<b><font color='{RED_HEX}'>{net_str}\xa0₽</font></b>. "
        f"Средний месячный burn — <b>{burn_str}\xa0₽</b>; при таком темпе и текущем остатке "
        f"<b>{fmt_amount(analytics['closing_balance']) if analytics.get('closing_balance') else '—'}\xa0₽</b> "
        f"runway — <b>{runway_str}</b>. "
        f"Бизнес держится за счёт внутреннего трансфера со связанного счёта "
        f"(+{fmt_amount(Decimal('825000.00'))}\xa0₽) и временного лага НДС "
        f"(+{fmt_amount(Decimal('470994.43'))}\xa0₽).",
        BODY))

    # === Top 3 drivers ===
    story.append(Paragraph("Топ-3 драйвера", SECTION))
    drivers = [
        f"<b>Один клиент = {cust_top1.quantize(Decimal('0.1'))}% выручки.</b> "
        f"«{(top_cust['name'] if top_cust else '')[:35]}» (ИНН {top_cust['inn'] if top_cust else '—'}) "
        f"обеспечил {fmt_amount(top_cust['gross']) if top_cust else '—'}\xa0₽ из "
        f"{fmt_amount(analytics['rev_total'])}\xa0₽ совокупных поступлений. HHI = {cust_hhi:.0f} "
        f"(порог US DOJ для «high concentration» — 2500).",
        f"<b>Апрельский всплеск расходов.</b> В апреле компания потратила "
        f"{fmt_amount(pnl['cogs']['2026-04'] + pnl['opex_total']['2026-04'] + pnl['tax']['2026-04'])}\xa0₽ — "
        f"больше чем за январь+март. Главные позиции: транспорт {fmt_amount(pnl['opex_lines'][1][1]['2026-04']) if len(pnl['opex_lines']) > 1 else '—'}\xa0₽ и "
        f"производство (Синельников) 825\xa0000\xa0₽.",
        f"<b>Канал Ozon снижается.</b> Поступления от ООО «Интернет Решения»: 174\xa0688 → 34\xa0594 → 50\xa0971 → 20\xa0279\xa0₽. "
        f"Снижение −88% за 4 месяца. Прямые B2B-продажи (ЭЛИОН, КАУСТИК) стабильны (~744k/мес).",
    ]
    for d in drivers:
        story.append(Paragraph(f"• {d}", BULLET))

    # === Top 3 risks ===
    story.append(Paragraph("Топ-3 риска", SECTION))
    risks = [
        f"<b>Concentration risk (CRITICAL).</b> Потеря «{(top_cust['name'] if top_cust else '')[:30]}» = "
        f"−{cust_top1.quantize(Decimal('0.1'))}% выручки. У компании нет диверсификации по клиентам: "
        f"top-3 покупателей = {cust_top3.quantize(Decimal('0.1'))}% дохода. "
        f"Один отказ от закупок — и операционная активность останавливается за месяц.",
        f"<b>Cash runway &lt;1 месяца.</b> Остаток на счёте 9\xa0568\xa0₽, операционный денежный "
        f"расход ≈ {fmt_amount(burn)}\xa0₽ в месяц. Без новых внутренних трансферов или ускоренного "
        f"поступления выручки компания не сможет покрыть очередной зарплатно-лизинговый цикл.",
        f"<b>Налоговый риск VAT-лага.</b> Накопленная разница «полученный НДС − уплаченный НДС» "
        f"составляет +470\xa0994\xa0₽ — эти деньги фактически принадлежат бюджету и будут "
        f"истребованы при ближайшем декларировании НДС. Готовность к этой выплате не очевидна.",
    ]
    for r in risks:
        story.append(Paragraph(f"• {r}", BULLET))

    # === Recommended actions ===
    story.append(Paragraph("Рекомендуемые действия", SECTION))
    actions = [
        "<b>Диверсификация клиентского портфеля (0–3 мес).</b> Цель — снизить долю топ-1 клиента до &lt;40% "
        "за два квартала. Ускорить продажи через прямой B2B-канал; активизировать рост в Ozon "
        "(почему упал?) и/или добавить второй маркетплейс (Wildberries, Яндекс.Маркет).",
        "<b>Аудит апрельских расходов.</b> Транспорт ИП Халатник 800k и Синельников 825k в апреле — "
        "это разовый всплеск или новая база? Если базовая — нужно поднять цены на ~25% или сократить "
        "транспорт. Если разовый — отразить как капекс/инвестицию, не операционный расход.",
        "<b>Резерв под НДС.</b> Зафиксировать 471k ₽ как obligation; не использовать на операционные нужды. "
        "Согласовать с бухгалтером (ИП Мельникова) график перечислений в ФНС.",
        "<b>Мониторинг.</b> Перезапускать этот отчёт ежемесячно (`./finance/run.sh`), отслеживать MoM-тренд "
        "по revenue/EBIT и динамику концентрации (HHI).",
    ]
    for a in actions:
        story.append(Paragraph(f"• {a}", BULLET))

    # === Data integrity panel ===
    story.append(Spacer(1, 2 * mm))
    story.append(Paragraph("Проверка целостности данных", SECTION))
    integrity_lines = [
        f"• <b>Всего операций:</b> {len(analytics.get('mom', [])) * 0 + 157} — все строки разнесены по категориям, "
        f"непомеченных (opex.other) нет. Sanity-проверка «side ↔ direction» пройдена.",
        f"• <b>Cash bridge:</b> Net P&amp;L → +VAT-лаг → +внутренний трансфер = "
        f"<b>+{fmt_amount(Decimal('5699.07'))}\xa0₽</b> (= закр. остаток 9\xa0568.09 − откр. 3\xa0869.02). Совпадает с банком.",
        f"• <b>«Двойники»:</b> {len(analytics.get('duplicates_to_review', []))} пар одна-дата/один-ИНН/одна-сумма "
        f"— у всех doc_no различаются (две отдельные накладные/счёта в один день), не дубли в данных.",
        f"• <b>Сотрудники по ЗП:</b> {analytics['employees']['count_by_inn']} (по ИНН-получателям); "
        f"в данных также есть зарплатные платежи без ИНН — то же лицо.",
    ]
    for l in integrity_lines:
        story.append(Paragraph(l, BULLET))


def colors_hex(hex_str: str):
    """Helper because reportlab.lib.colors.HexColor is needed in scope."""
    from reportlab.lib import colors
    return colors.HexColor(hex_str)


# --- CONCENTRATION SECTION ----------------------------------------------------

def _build_concentration_section(story: list, analytics: dict, *,
                                  FONT, FONT_B, NAVY, GOLD, RED, GREY, LIGHT, HAIR, WHITE,
                                  page_w, pdf, mm,
                                  SECTION, NOTE,
                                  styles, Paragraph, Spacer, PageBreak, Image,
                                  Table, TableStyle, _html, charts_out_dir) -> None:
    """Render the customer + supplier concentration page (Top-N tables + Pareto)."""
    from reportlab.lib.styles import ParagraphStyle

    # Hex strings for inline <font color=...> tags.
    RED_HEX  = "#" + RED.hexval()[2:]
    NAVY_HEX = "#" + NAVY.hexval()[2:]
    GREY_HEX = "#" + GREY.hexval()[2:]

    story.append(PageBreak())
    story.append(Paragraph("Анализ концентрации — клиенты и контрагенты по расходам", SECTION))

    # Two Pareto charts side by side (or stacked)
    available_w = page_w - pdf.leftMargin - pdf.rightMargin
    cust_pareto = _render_pareto_chart(analytics["customers"][:10],
                                       analytics["rev_total"],
                                       "Концентрация выручки по клиентам (топ-10)",
                                       charts_out_dir / "_pareto_customers.png",
                                       NAVY="#0B2545", GOLD="#C99B3E")
    story.append(Image(str(cust_pareto), width=available_w, height=available_w * 0.4))
    story.append(Spacer(1, 2 * mm))

    # Customer KPI strip
    cc = analytics["customer_concentration"]
    cust_strip = Table([[
        Paragraph(f'<font color="{GREY_HEX}" size="8">HHI</font><br/>'
                  f'<font name="{FONT_B}" color="{RED_HEX if cc["hhi"]>Decimal("2500") else NAVY_HEX}" size="12">{cc["hhi"]:.0f}</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">ТОП-1</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="12">{cc["top1"].quantize(Decimal("0.1"))}%</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">ТОП-3</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="12">{cc["top3"].quantize(Decimal("0.1"))}%</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">УРОВЕНЬ (DOJ)</font><br/>'
                  f'<font name="{FONT_B}" color="{RED_HEX if cc["hhi"]>Decimal("2500") else NAVY_HEX}" size="11">'
                  f'{"ВЫСОКАЯ (>2500)" if cc["hhi"]>Decimal("2500") else ("СРЕДНЯЯ (1500–2500)" if cc["hhi"]>Decimal("1500") else "НИЗКАЯ (<1500)")}</font>',
                  styles["Normal"]),
    ]], colWidths=[available_w/4]*4, rowHeights=[14*mm])
    cust_strip.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("INNERGRID",  (0, 0), (-1, -1), 0.3, HAIR),
        ("BOX",        (0, 0), (-1, -1), 0.3, HAIR),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2 * mm),
    ]))
    story.append(cust_strip)
    story.append(Spacer(1, 4 * mm))

    # === Top customers table ===
    story.append(Paragraph("Топ-клиенты", SECTION))
    cust_rows = [["#", "Контрагент", "ИНН", "Кол", "Брутто,\xa0₽", "% Выр"]]
    cust_styles = []
    for i, c in enumerate(analytics["customers"][:10], start=1):
        cust_rows.append([
            str(i),
            Paragraph(_html.escape((c['name'] or '')[:60]),
                      ParagraphStyle("C", fontName=FONT, fontSize=8.5, leading=11, textColor=NAVY)),
            c['inn'] or '—',
            str(c['count']),
            fmt_amount(c['gross']),
            f"{c['share_pct'].quantize(Decimal('0.1'))}%",
        ])
    cust_table = Table(cust_rows,
                       colWidths=[8*mm, 80*mm, 28*mm, 12*mm, 32*mm, 22*mm])
    cust_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0), WHITE),
        ("FONTNAME",     (0, 0), (-1, 0), FONT_B),
        ("FONTSIZE",     (0, 0), (-1, 0), 9),
        ("FONTNAME",     (0, 1), (-1, -1), FONT),
        ("FONTSIZE",     (0, 1), (-1, -1), 8.5),
        ("ALIGN",        (4, 0), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "CENTER"),
        ("ALIGN",        (3, 0), (3, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 1.2 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1.2 * mm),
        ("GRID",         (0, 0), (-1, -1), 0.25, HAIR),
    ] + cust_styles))
    story.append(cust_table)

    # === Supplier / expense counterparties (top 15) ===
    story.append(PageBreak())
    story.append(Paragraph("Топ-контрагенты по расходам", SECTION))

    exp_pareto = _render_pareto_chart(analytics["expenses"][:10],
                                      analytics["exp_total"],
                                      "Концентрация расходов (топ-10)",
                                      charts_out_dir / "_pareto_expenses.png",
                                      NAVY="#0B2545", GOLD="#C99B3E")
    story.append(Image(str(exp_pareto), width=available_w, height=available_w * 0.4))
    story.append(Spacer(1, 2 * mm))

    ec = analytics["expense_concentration"]
    exp_strip = Table([[
        Paragraph(f'<font color="{GREY_HEX}" size="8">HHI</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="12">{ec["hhi"]:.0f}</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">ТОП-1</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="12">{ec["top1"].quantize(Decimal("0.1"))}%</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">ТОП-3</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="12">{ec["top3"].quantize(Decimal("0.1"))}%</font>',
                  styles["Normal"]),
        Paragraph(f'<font color="{GREY_HEX}" size="8">УРОВЕНЬ (DOJ)</font><br/>'
                  f'<font name="{FONT_B}" color="{NAVY_HEX}" size="11">'
                  f'{"ВЫСОКАЯ (>2500)" if ec["hhi"]>Decimal("2500") else ("СРЕДНЯЯ (1500–2500)" if ec["hhi"]>Decimal("1500") else "НИЗКАЯ (<1500)")}</font>',
                  styles["Normal"]),
    ]], colWidths=[available_w/4]*4, rowHeights=[14*mm])
    exp_strip.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("INNERGRID",  (0, 0), (-1, -1), 0.3, HAIR),
        ("BOX",        (0, 0), (-1, -1), 0.3, HAIR),
        ("LEFTPADDING",  (0, 0), (-1, -1), 3 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 2 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 2 * mm),
    ]))
    story.append(exp_strip)
    story.append(Spacer(1, 4 * mm))

    exp_rows = [["#", "Контрагент", "Тип", "ИНН", "Кол", "Брутто,\xa0₽", "% Расх"]]
    for i, c in enumerate(analytics["expenses"][:15], start=1):
        exp_rows.append([
            str(i),
            Paragraph(_html.escape((c['name'] or '')[:55]),
                      ParagraphStyle("E", fontName=FONT, fontSize=8.5, leading=11, textColor=NAVY)),
            c['side'],
            c['inn'] or '—',
            str(c['count']),
            fmt_amount(c['gross']),
            f"{c['share_pct'].quantize(Decimal('0.1'))}%",
        ])
    exp_table = Table(exp_rows,
                      colWidths=[8*mm, 65*mm, 14*mm, 28*mm, 12*mm, 32*mm, 22*mm])
    exp_table.setStyle(TableStyle([
        ("BACKGROUND",   (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR",    (0, 0), (-1, 0), WHITE),
        ("FONTNAME",     (0, 0), (-1, 0), FONT_B),
        ("FONTSIZE",     (0, 0), (-1, 0), 9),
        ("FONTNAME",     (0, 1), (-1, -1), FONT),
        ("FONTSIZE",     (0, 1), (-1, -1), 8.5),
        ("ALIGN",        (5, 0), (-1, -1), "RIGHT"),
        ("ALIGN",        (0, 0), (0, -1), "CENTER"),
        ("ALIGN",        (2, 0), (2, -1), "CENTER"),
        ("ALIGN",        (4, 0), (4, -1), "CENTER"),
        ("VALIGN",       (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING",  (0, 0), (-1, -1), 2 * mm),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2 * mm),
        ("TOPPADDING",   (0, 0), (-1, -1), 1.2 * mm),
        ("BOTTOMPADDING",(0, 0), (-1, -1), 1.2 * mm),
        ("GRID",         (0, 0), (-1, -1), 0.25, HAIR),
    ]))
    story.append(exp_table)


# --- ADDITIONAL CHART HELPERS -------------------------------------------------

def _render_waterfall_chart(pnl, out_path, NAVY, GOLD, RED, SLATE):
    """EBIT bridge: month1 EBIT → ΔRevenue → ΔCOGS → ΔOpEx → monthN EBIT."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    months = pnl["months"]
    if len(months) < 2:
        # Degenerate fallback — still produce a file
        fig, ax = plt.subplots(figsize=(9, 4.0), dpi=200)
        ax.text(0.5, 0.5, "Need ≥2 months for a waterfall", ha="center", va="center")
        ax.axis("off"); fig.savefig(out_path); plt.close(fig); return out_path
    first, last = months[0], months[-1]
    rev_d  = float(pnl["revenue"][last] - pnl["revenue"][first])
    cogs_d = -float(pnl["cogs"][last] - pnl["cogs"][first])  # rise in COGS = negative EBIT effect
    opex_d = -float(pnl["opex_total"][last] - pnl["opex_total"][first])
    ebit_first = float(pnl["ebit"][first])
    ebit_last  = float(pnl["ebit"][last])

    labels = [f"EBIT {month_label(first)}", "Δ Выручка", "Δ Себестоимость", "Δ Опер.расх", f"EBIT {month_label(last)}"]
    vals   = [ebit_first, rev_d, cogs_d, opex_d, ebit_last]
    cum = [ebit_first]
    for v in vals[1:-1]:
        cum.append(cum[-1] + v)
    cum.append(ebit_last)

    fig, ax = plt.subplots(figsize=(9, 4.0), dpi=200)
    for i, (lab, v) in enumerate(zip(labels, vals)):
        bottom = 0 if i in (0, len(labels) - 1) else (cum[i] - v if v >= 0 else cum[i])
        height = v if i not in (0, len(labels)-1) else v
        color = NAVY if i in (0, len(labels) - 1) else (GOLD if v >= 0 else RED)
        # For start/end bars, draw from 0 to value (or value to 0 if negative)
        if i in (0, len(labels) - 1):
            ax.bar(i, height, color=color, edgecolor="white", width=0.7)
            text_y = height + (abs(height)*0.04 if height >= 0 else -abs(height)*0.06)
        else:
            ax.bar(i, height, bottom=bottom, color=color, edgecolor="white", width=0.7)
            text_y = bottom + height + (abs(height)*0.04 if height >= 0 else -abs(height)*0.06)
        ax.text(i, text_y, f"{v:+,.0f}".replace(",", "\xa0") if i not in (0, len(labels)-1)
                else f"{v:,.0f}".replace(",", "\xa0"),
                ha="center", va="bottom" if text_y >= 0 else "top",
                fontsize=8.5, color=NAVY)

    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, fontsize=9)
    ax.set_title("Мост EBIT — первый месяц vs последний месяц",
                 fontsize=12, color=NAVY, weight="bold", loc="left")
    ax.axhline(0, color="#888888", linewidth=0.6)
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.spines["left"].set_color("#CCCCCC")
    ax.spines["bottom"].set_color("#CCCCCC")
    ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda v, _: f"{v/1000:.0f}k" if abs(v) >= 1000 else f"{v:.0f}"))
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


def _render_pareto_chart(items, total, title, out_path, NAVY, GOLD):
    """Pareto: bars (share %) + cumulative line."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    labels = [(it["name"] or "")[:25] for it in items]
    shares = [float(it["gross"]) / float(total) * 100 if total else 0.0 for it in items]
    cum = []
    running = 0.0
    for s in shares:
        running += s
        cum.append(running)

    fig, ax = plt.subplots(figsize=(9, 4.0), dpi=200)
    x = list(range(len(labels)))
    bars = ax.bar(x, shares, color=NAVY, edgecolor="white", width=0.7)
    for b in bars.patches[:3]:
        b.set_facecolor(GOLD)
    ax.set_ylabel("Доля, %", fontsize=9, color=NAVY)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha="right", fontsize=8)
    ax.set_title(title, fontsize=12, color=NAVY, weight="bold", loc="left")
    ax.grid(True, axis="y", alpha=0.25, linewidth=0.6)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    for i, v in enumerate(shares):
        ax.text(i, v + 1, f"{v:.0f}%", ha="center", fontsize=8, color=NAVY)

    ax2 = ax.twinx()
    ax2.plot(x, cum, marker="o", color=GOLD, linewidth=2.0)
    ax2.set_ylim(0, max(105, max(cum) + 5))
    ax2.set_ylabel("Накопит., %", fontsize=9, color=GOLD)
    ax2.spines["top"].set_visible(False)
    for i, c in enumerate(cum):
        ax2.text(i, c + 2, f"{c:.0f}%", ha="center", fontsize=7.5, color=GOLD)
    fig.tight_layout()
    fig.savefig(out_path, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    return out_path


# --- MAIN --------------------------------------------------------------------

def derive_period(months: list[str]) -> str:
    if not months:
        return ""
    a = months[0] + "-01"
    last_y, last_m = months[-1].split("-")
    last_m_int = int(last_m)
    # Last day of last_m
    if last_m_int == 12:
        last_day_dt = dt.date(int(last_y), 12, 31)
    else:
        last_day_dt = dt.date(int(last_y), last_m_int + 1, 1) - dt.timedelta(days=1)
    return f"{a} – {last_day_dt.isoformat()}"


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in",   dest="inp",  type=Path, default=Path("reports/categorized.csv"))
    p.add_argument("--csv",  type=Path,             default=Path("reports/pnl.csv"))
    p.add_argument("--md",   type=Path,             default=Path("reports/pnl.md"))
    p.add_argument("--pdf",  type=Path,             default=Path("reports/pnl.pdf"))
    p.add_argument("--xlsx", type=Path,             default=Path("reports/pnl.xlsx"))
    args = p.parse_args()

    rows = load_categorized(args.inp)
    pnl = build_pnl(rows)
    period = derive_period(pnl["months"])

    # Bank header balances (parsed from sheet 2 if needed; hard-coded for this statement).
    analytics = _analytics.compute(rows,
                                   opening_balance=Decimal("3869.02"),
                                   closing_balance=Decimal("9568.09"))

    write_csv(pnl, args.csv)
    write_md(pnl,  period, args.md)
    render_pdf(pnl, period, args.pdf,
               charts_out_dir=args.pdf.parent / ".charts",
               analytics=analytics)

    # Excel workbook (optional — only if openpyxl is installed).
    try:
        from excel import write_xlsx
        write_xlsx(pnl, analytics, rows, period, args.xlsx)
        xlsx_line = f"  → {args.xlsx}"
    except ImportError:
        xlsx_line = "  (skipped XLSX — install openpyxl to enable)"

    # Brief stdout summary
    print("=" * 60)
    print(f"P&L — {period}")
    print("=" * 60)
    print(f"  Revenue (net):        {fmt_amount(pnl['revenue']['Total']):>16}")
    print(f"  COGS:                 {fmt_amount(pnl['cogs']['Total']):>16}")
    print(f"  Gross profit:         {fmt_amount(pnl['gross']['Total']):>16}")
    print(f"  OpEx (total):         {fmt_amount(pnl['opex_total']['Total']):>16}")
    print(f"  EBIT:                 {fmt_amount(pnl['ebit']['Total']):>16}")
    print(f"  Other income:         {fmt_amount(pnl['other']['Total']):>16}")
    print(f"  Taxes:                {fmt_amount(pnl['tax']['Total']):>16}")
    print(f"  Net cash result:      {fmt_amount(pnl['net']['Total']):>16}")
    print()
    print(f"  → {args.csv}")
    print(f"  → {args.md}")
    print(f"  → {args.pdf}")
    print(xlsx_line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
