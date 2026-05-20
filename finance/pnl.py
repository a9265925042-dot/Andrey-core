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

# --- P&L STRUCTURE -----------------------------------------------------------
# Each line: (label, level [0 header, 1 sub, 2 total], list of categories OR
# a computed-formula function (computed at aggregation time))

REVENUE_CATS = ["revenue.marketplace_ozon", "revenue.direct_b2b"]
COGS_CATS    = ["cogs.goods", "cogs.manufacturing", "cogs.parts"]
OPEX_GROUPS  = [
    ("Salaries",           ["opex.salary"]),
    ("Transport",          ["opex.transport"]),
    ("Shipping",           ["opex.shipping"]),
    ("Leasing (auto)",     ["opex.leasing_auto"]),
    ("Insurance (OSAGO)",  ["opex.insurance_osago"]),
    ("Vehicle maintenance",["opex.vehicle_maint"]),
    ("Rent",               ["opex.rent"]),
    ("Utilities",          ["opex.utilities"]),
    ("Telecom",            ["opex.telecom"]),
    ("Software / IT",      ["opex.software"]),
    ("Accounting",         ["opex.accounting"]),
    ("Training",           ["opex.training"]),
    ("Bank fees",          ["opex.bank_fees"]),
    ("Other",              ["opex.other"]),
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
        ("Net P&L result (net of VAT)",                   net_pnl),
        ("(+) VAT collected − VAT paid (timing lag)",     vat_kept),
        ("= External cash result",                        external_cash),
        ("(+) Internal transfers from / to own accounts", internal),
        ("= Net bank cash change",                        bank_change),
    ]


# --- FORMATTING --------------------------------------------------------------

def month_label(yyyymm: str, ru: bool = False) -> str:
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
    cols = ["Line"] + [month_label(m) for m in months] + ["Total"]
    rows = []
    def push(label: str, d: dict[str, Decimal]):
        rows.append([label] + [f"{d.get(m, DEC0):.2f}" for m in months] + [f"{d['Total']:.2f}"])

    rows.append(["= Revenue (net of VAT) ="])
    push("  Revenue (total)", pnl["revenue"])
    for n, d in pnl["revenue_sub"]:
        push(f"    {n}", d)
    rows.append(["= COGS ="])
    push("  COGS (total)", pnl["cogs"])
    for n, d in pnl["cogs_sub"]:
        push(f"    {n}", d)
    push("Gross profit", pnl["gross"])
    rows.append(["= Operating expenses ="])
    for label, d in pnl["opex_lines"]:
        push(f"  {label}", d)
    push("OpEx (total)", pnl["opex_total"])
    push("Operating profit (EBIT)", pnl["ebit"])
    push("Other income", pnl["other"])
    push("Taxes (total)", pnl["tax"])
    for n, d in pnl["tax_sub"]:
        push(f"  {n}", d)
    push("Net cash result", pnl["net"])

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
    hdr_cols = ["Line"] + [month_label(m) for m in months] + ["Total"]
    sep      = ["---"] + ["---:"] * (len(hdr_cols) - 1)

    def row(label: str, d: dict[str, Decimal], total_label: str | None = None) -> str:
        vals = [fmt_amount(d.get(m, DEC0)) for m in months] + [fmt_amount(d["Total"])]
        return "| " + " | ".join([label, *vals]) + " |"

    lines: list[str] = []
    lines.append(f"# Cash-basis P&L — {period}")
    lines.append("")
    lines.append("> **Basis:** cash (по фактическому движению денег), валюта **RUB**. ")
    lines.append("> Источник: банковская выписка р/с 40702810102500117620 (ООО «ТЕХСОЛ»). ")
    lines.append("> Внутренние переводы между собственными счетами исключены. НДС в составе платежей выделяется из назначения.")
    lines.append("")
    lines.append("| " + " | ".join(hdr_cols) + " |")
    lines.append("| " + " | ".join(sep) + " |")
    lines.append(row("**Revenue (net of VAT)**", pnl["revenue"]))
    for n, d in pnl["revenue_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**COGS**", pnl["cogs"]))
    for n, d in pnl["cogs_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**Gross profit**", pnl["gross"]))
    gp_pct = [fmt_pct(pnl["gross"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
             [fmt_pct(pnl["gross"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Gross margin % | " + " | ".join(gp_pct) + " |")
    for label, d in pnl["opex_lines"]:
        lines.append(row(f"&nbsp;&nbsp;{label}", d))
    lines.append(row("**OpEx (total)**", pnl["opex_total"]))
    lines.append(row("**Operating profit (EBIT)**", pnl["ebit"]))
    op_pct = [fmt_pct(pnl["ebit"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
             [fmt_pct(pnl["ebit"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Operating margin % | " + " | ".join(op_pct) + " |")
    lines.append(row("Other income", pnl["other"]))
    lines.append(row("Taxes (total)", pnl["tax"]))
    for n, d in pnl["tax_sub"]:
        lines.append(row(f"&nbsp;&nbsp;{n}", d))
    lines.append(row("**Net cash result**", pnl["net"]))
    net_pct = [fmt_pct(pnl["net"].get(m, DEC0), pnl["revenue"].get(m, DEC0)) for m in months] + \
              [fmt_pct(pnl["net"]["Total"], pnl["revenue"]["Total"])]
    lines.append("| Net margin % | " + " | ".join(net_pct) + " |")
    lines.append("")
    lines.append("## Cash reconciliation (P&L → bank)")
    lines.append("")
    lines.append("| Bridge step | Amount |")
    lines.append("| --- | ---: |")
    for label, value in cash_bridge(pnl):
        bold = label.startswith("=")
        lab = f"**{label}**" if bold else label
        val = f"**{fmt_amount(value)}**" if bold else fmt_amount(value)
        lines.append(f"| {lab} | {val} |")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- **Cash basis only.** Выручка/расходы здесь — это фактически полученные/потраченные деньги, не начисления.")
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


def render_pdf(pnl: dict, period: str, out: Path, charts_out_dir: Path) -> None:
    """Render a best-in-class P&L PDF using reportlab + matplotlib charts."""
    import html as _html
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        BaseDocTemplate, Frame, Image, PageTemplate, PageBreak,
        Paragraph, Spacer, Table, TableStyle,
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
                          "Cash basis (bank statement). Internal transfers excluded. VAT extracted from purpose.")
        canvas.drawRightString(page_w - pdf.rightMargin, 9 * mm, f"Page {_doc.page}")
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

    # === Cover band ===
    story.append(Paragraph("Profit &amp; Loss — Cash Basis", H1))
    story.append(Paragraph(f"ООО «ТЕХСОЛ» &nbsp;·&nbsp; Account 40702810102500117620 (RUB) &nbsp;·&nbsp; {period}",
                           SUBTITLE))

    # === KPI tiles ===
    rev_t  = pnl["revenue"]["Total"]
    gp_t   = pnl["gross"]["Total"]
    ebit_t = pnl["ebit"]["Total"]
    net_t  = pnl["net"]["Total"]
    gm_pct = (gp_t / rev_t * 100) if rev_t else Decimal("0")
    om_pct = (ebit_t / rev_t * 100) if rev_t else Decimal("0")
    nm_pct = (net_t / rev_t * 100) if rev_t else Decimal("0")

    TILE_LBL = ParagraphStyle("TileLbl", fontName=FONT, fontSize=8, leading=10, textColor=GREY,
                              splitLongWords=False)
    TILE_VAL = ParagraphStyle("TileVal", fontName=FONT_B, fontSize=12, leading=15, textColor=NAVY,
                              splitLongWords=False)
    TILE_NEG = ParagraphStyle("TileNeg", fontName=FONT_B, fontSize=12, leading=15, textColor=RED,
                              splitLongWords=False)

    def tile(label, value_str, negative=False):
        return [
            Paragraph(label, TILE_LBL),
            Spacer(1, 1.5 * mm),
            Paragraph(value_str, TILE_NEG if negative else TILE_VAL),
        ]

    kpi = Table(
        [[
            tile("REVENUE",       f"{fmt_amount(rev_t)}\xa0₽"),
            tile("GROSS PROFIT",  f"{fmt_amount(gp_t)}\xa0₽",   negative=gp_t < 0),
            tile("EBIT",          f"{fmt_amount(ebit_t)}\xa0₽", negative=ebit_t < 0),
            tile("NET RESULT",    f"{fmt_amount(net_t)}\xa0₽",  negative=net_t < 0),
        ],
        [
            tile("GROSS MARGIN",     f"{gm_pct.quantize(Decimal('0.1'))}%", negative=gm_pct < 0),
            tile("OPERATING MARGIN", f"{om_pct.quantize(Decimal('0.1'))}%", negative=om_pct < 0),
            tile("NET MARGIN",       f"{nm_pct.quantize(Decimal('0.1'))}%", negative=nm_pct < 0),
            tile("PERIOD",           f"{len(months)} months"),
        ]],
        colWidths=[(page_w - pdf.leftMargin - pdf.rightMargin) / 4.0] * 4,
        rowHeights=[18 * mm, 18 * mm],
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

    # === Main P&L table ===
    cols = ["", *month_labels, "Total"]
    table_rows = [cols]
    styles_acc: list[tuple] = []

    def add_row(label, d, bold=False, indent=0, accent=False, italic=False):
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
        table_rows.append([label_p] + vals)
        idx = len(table_rows) - 1
        # Per-cell font + color (numbers as strings; no Paragraph wrapping issues).
        styles_acc.append(("FONTNAME", (1, idx), (-1, idx), font))
        styles_acc.append(("FONTSIZE", (1, idx), (-1, idx), 8.5))
        for col_idx, col in enumerate(cell_colors, start=1):
            styles_acc.append(("TEXTCOLOR", (col_idx, idx), (col_idx, idx), col))
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
        table_rows.append(cells)
        idx = len(table_rows) - 1
        styles_acc.append(("FONTNAME", (1, idx), (-1, idx), FONT))
        styles_acc.append(("FONTSIZE", (1, idx), (-1, idx), 8))
        styles_acc.append(("TEXTCOLOR", (1, idx), (-1, idx), GREY))

    # Build rows.
    add_row("Revenue (net of VAT)", pnl["revenue"], bold=True, accent=True)
    for n, d in pnl["revenue_sub"]:
        add_row(n.replace("_", " ").title(), d, indent=1)
    add_row("Cost of goods sold (COGS)", pnl["cogs"], bold=True, accent=True)
    for n, d in pnl["cogs_sub"]:
        add_row(n.replace("_", " ").title(), d, indent=1)
    add_row("Gross profit", pnl["gross"], bold=True)
    add_margin_row("Gross margin", pnl["gross"], pnl["revenue"])
    add_row("Operating expenses", pnl["opex_total"], bold=True, accent=True)
    for label, d in pnl["opex_lines"]:
        add_row(label, d, indent=1)
    add_row("Operating profit (EBIT)", pnl["ebit"], bold=True)
    add_margin_row("Operating margin", pnl["ebit"], pnl["revenue"])
    add_row("Other income", pnl["other"])
    add_row("Taxes (FNS + Social Fund)", pnl["tax"], bold=True, accent=True)
    for n, d in pnl["tax_sub"]:
        add_row(n.replace("_", " ").title(), d, indent=1)
    add_row("Net cash result", pnl["net"], bold=True)
    add_margin_row("Net margin", pnl["net"], pnl["revenue"])

    label_w = 50 * mm
    data_w = (page_w - pdf.leftMargin - pdf.rightMargin - label_w) / (len(months) + 1)
    col_widths = [label_w] + [data_w] * (len(months) + 1)
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
    story.append(Paragraph("Statement of operations", SECTION))
    story.append(pnl_table)

    # === Cash reconciliation bridge ===
    story.append(Spacer(1, 5 * mm))
    story.append(Paragraph("Cash reconciliation — P&amp;L → bank account", SECTION))
    bridge = cash_bridge(pnl)
    bridge_rows = [["Bridge step", "Amount, ₽"]]
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

    # === Page 2: Charts ===
    story.append(PageBreak())
    story.append(Paragraph("Monthly trend &amp; expense structure", SECTION))
    story.append(Spacer(1, 1 * mm))
    available_w = page_w - pdf.leftMargin - pdf.rightMargin
    story.append(Image(str(trend_png),   width=available_w, height=available_w * 0.5))
    story.append(Spacer(1, 4 * mm))
    story.append(Image(str(expense_png), width=available_w, height=available_w * 0.5))
    story.append(Spacer(1, 2 * mm))

    # === Notes / methodology ===
    notes_lines = [
        "<b>Methodology.</b> Cash-basis Profit &amp; Loss derived from a single bank account statement. "
        "Internal transfers between own accounts (same INN) are excluded. VAT is extracted from the payment "
        "purpose string &laquo;в т.ч. НДС N% xxx&raquo; where present; otherwise it is computed from the rule-defined rate.",
        "<b>Limitations.</b> Bank cash flow ≠ accrual revenue / expense. Receivables and payables (e.g. unpaid invoices) "
        "are not visible. Inventory movement is not captured. Salaries shown net of personal income tax; payroll taxes "
        "(NDFL + social) flow through the ФНС / ОСФР lines.",
        "<b>Source.</b> Bank statement, RUB account 40702810102500117620 (ООО «ТЕХСОЛ»), period as titled.",
    ]
    story.append(Spacer(1, 4 * mm))
    story.append(Paragraph("Notes", SECTION))
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
    ax.plot(x, rev,  label="Revenue",     marker="o", color=NAVY,  linewidth=2.2)
    ax.plot(x, gp,   label="Gross profit",marker="o", color=GOLD,  linewidth=2.0)
    ax.plot(x, ebit, label="EBIT",        marker="o", color=SLATE, linewidth=1.8, linestyle="--")
    ax.plot(x, net,  label="Net result",  marker="o", color=RED,   linewidth=1.8, linestyle=":")
    ax.set_title("Monthly trend — Revenue, Gross, EBIT, Net", fontsize=12, color=NAVY, weight="bold", loc="left")
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
    items += [("Taxes", float(pnl["tax"]["Total"]))]
    items += [("COGS",  float(pnl["cogs"]["Total"]))]
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
    args = p.parse_args()

    rows = load_categorized(args.inp)
    pnl = build_pnl(rows)
    period = derive_period(pnl["months"])

    write_csv(pnl, args.csv)
    write_md(pnl,  period, args.md)
    render_pdf(pnl, period, args.pdf, charts_out_dir=args.pdf.parent / ".charts")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
