#!/usr/bin/env python3
"""
analytics.py — counterparty concentration, MoM trends, anomaly detection, runway.

Produces a single Python dict (returned by `compute(categorized_rows)`) that
`pnl.py` consumes when rendering the executive-grade PDF.

Definitions used (matching standard FP&A practice):
  - HHI: Herfindahl-Hirschman Index, sum of squared market shares ×10000.
        <1500 = unconcentrated, 1500–2500 = moderate, >2500 = high (US DOJ scale).
  - Coefficient of Variation (CV): std / mean. CV>1.0 means the series is more
        volatile than its mean — useful for flagging lumpy categories.
  - Runway: how many months the closing cash balance covers the average monthly
        burn (negative net P&L). 0 means immediate funding need.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

DEC0 = Decimal("0")


def _dec(s: str) -> Decimal:
    s = (s or "").strip()
    if not s:
        return DEC0
    try:
        return Decimal(s)
    except Exception:
        return DEC0


def compute(rows: list[dict], opening_balance: Decimal | None = None,
            closing_balance: Decimal | None = None) -> dict:
    """Return analytics blob: concentration, top-N, MoM, anomalies, runway."""

    months_set: set[str] = set()
    by_cp = defaultdict(lambda: {
        "name": "", "inn": "", "side": "", "category": "",
        "count": 0, "gross": DEC0, "net": DEC0,
    })
    by_cat_month: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: DEC0))
    by_side_month: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(lambda: DEC0))

    same_day_amount_inn: dict[tuple, list[dict]] = defaultdict(list)

    for r in rows:
        if r["side"] == "internal_transfer":
            continue
        m = r["op_date"][:7]
        if not m:
            continue
        months_set.add(m)
        gross_d = _dec(r["debit"])
        gross_c = _dec(r["credit"])
        gross = gross_c if r["side"] in ("revenue", "other_income") else gross_d
        net   = _dec(r["net_amount"])

        cp_key = (r["side"], r["inn"] or f"__noinn__{r['counterparty'][:20]}")
        d = by_cp[cp_key]
        d["name"] = r["counterparty"]
        d["inn"]  = r["inn"]
        d["side"] = r["side"]
        d["category"] = r["category"]
        d["count"] += 1
        d["gross"] += gross
        d["net"]   += net

        by_cat_month[r["category"]][m] += net
        by_side_month[r["side"]][m]    += net

        amt_key = r["debit"] or r["credit"]
        if amt_key and _dec(amt_key) > 0:
            same_day_amount_inn[(r["op_date"], r["inn"], amt_key)].append(r)

    months = sorted(months_set)

    # --- Customers ---------------------------------------------------------
    custs = [v for v in by_cp.values() if v["side"] == "revenue"]
    custs.sort(key=lambda v: -v["gross"])
    rev_total = sum(v["gross"] for v in custs)
    customer_rows = []
    for v in custs:
        share = (v["gross"] / rev_total * 100) if rev_total else DEC0
        customer_rows.append({**v, "share_pct": share})
    cust_hhi = sum((v["gross"] / rev_total) ** 2 for v in custs) * 10000 if rev_total else DEC0
    cust_top1 = (custs[0]["gross"] / rev_total * 100) if custs and rev_total else DEC0
    cust_top3 = sum(v["gross"] for v in custs[:3]) / rev_total * 100 if rev_total else DEC0

    # --- Suppliers / expense counterparties --------------------------------
    exps = [v for v in by_cp.values() if v["side"] in ("opex", "cogs", "tax")]
    exps.sort(key=lambda v: -v["gross"])
    exp_total = sum(v["gross"] for v in exps)
    expense_rows = []
    for v in exps:
        share = (v["gross"] / exp_total * 100) if exp_total else DEC0
        expense_rows.append({**v, "share_pct": share})
    exp_hhi = sum((v["gross"] / exp_total) ** 2 for v in exps) * 10000 if exp_total else DEC0
    exp_top1 = (exps[0]["gross"] / exp_total * 100) if exps and exp_total else DEC0
    exp_top3 = sum(v["gross"] for v in exps[:3]) / exp_total * 100 if exp_total else DEC0

    # --- MoM analytics -----------------------------------------------------
    mom_lines = []
    rev_by_m = by_side_month["revenue"]
    cogs_by_m = by_side_month["cogs"]
    opex_by_m = by_side_month["opex"]
    tax_by_m  = by_side_month["tax"]
    oth_by_m  = by_side_month["other_income"]
    prev_rev = None
    for m in months:
        d = {"month": m,
             "revenue": rev_by_m.get(m, DEC0),
             "cogs":    cogs_by_m.get(m, DEC0),
             "opex":    opex_by_m.get(m, DEC0),
             "tax":     tax_by_m.get(m, DEC0),
             "other":   oth_by_m.get(m, DEC0)}
        d["ebit"]   = d["revenue"] - d["cogs"] - d["opex"]
        d["net"]    = d["ebit"] + d["other"] - d["tax"]
        d["mom_rev_pct"] = ((d["revenue"] - prev_rev) / prev_rev * 100) if (prev_rev and prev_rev != 0) else None
        prev_rev = d["revenue"]
        mom_lines.append(d)

    # --- Volatility per category ------------------------------------------
    vol_lines = []
    for cat, mp in by_cat_month.items():
        vals = [float(mp.get(m, DEC0)) for m in months]
        if not any(vals):
            continue
        avg = sum(vals) / len(vals)
        var = sum((v - avg) ** 2 for v in vals) / len(vals)
        std = var ** 0.5
        cv = (std / abs(avg)) if avg else 0.0
        vol_lines.append({"category": cat, "avg": Decimal(f"{avg:.2f}"),
                          "std": Decimal(f"{std:.2f}"), "cv": cv,
                          "series": [Decimal(f"{v:.2f}") for v in vals]})
    # Anomalous = CV > 1.0 AND not zero-mean.
    vol_lines.sort(key=lambda v: -v["cv"])
    anomalies = [v for v in vol_lines if v["cv"] > 1.0 and v["avg"] != 0]

    # --- Possible duplicates (same date + INN + amount) -------------------
    dup_groups = []
    for key, items in same_day_amount_inn.items():
        if len(items) >= 2:
            # Only flag if counterparty matches (sanity) and doc numbers differ — those are 2 legit
            # invoices, but worth showing to user as "review".
            dup_groups.append({
                "date": key[0], "inn": key[1], "amount": key[2],
                "count": len(items),
                "name": (items[0].get("counterparty") or "")[:60],
                "doc_nos": [it.get("doc_no", "") for it in items],
                "purposes": [(it.get("purpose") or "")[:80] for it in items],
            })
    dup_groups.sort(key=lambda v: v["date"])

    # --- Employees (salary recipients) -----------------------------------
    employee_inns: set[str] = set()
    employee_names: set[str] = set()
    for r in rows:
        if r["category"] == "opex.salary":
            employee_names.add(r["counterparty"].split()[0].upper() + " " + r["counterparty"].split()[1].upper()
                               if len(r["counterparty"].split()) >= 2 else r["counterparty"])
            if r["inn"] and r["inn"] != "0":
                employee_inns.add(r["inn"])

    # --- Burn / runway ---------------------------------------------------
    avg_rev  = sum(rev_by_m.get(m, DEC0)  for m in months) / len(months) if months else DEC0
    avg_cogs = sum(cogs_by_m.get(m, DEC0) for m in months) / len(months) if months else DEC0
    avg_opex = sum(opex_by_m.get(m, DEC0) for m in months) / len(months) if months else DEC0
    avg_tax  = sum(tax_by_m.get(m, DEC0)  for m in months) / len(months) if months else DEC0
    avg_oth  = sum(oth_by_m.get(m, DEC0)  for m in months) / len(months) if months else DEC0
    avg_net  = avg_rev + avg_oth - avg_cogs - avg_opex - avg_tax  # negative = burn
    burn_per_month = -avg_net if avg_net < 0 else DEC0
    runway_months = (closing_balance / burn_per_month) if (closing_balance and burn_per_month > 0) else None

    # --- Annualised projection -------------------------------------------
    annual = {
        "revenue":  avg_rev * 12,
        "cogs":     avg_cogs * 12,
        "opex":     avg_opex * 12,
        "tax":      avg_tax * 12,
        "ebit":     (avg_rev - avg_cogs - avg_opex) * 12,
        "net":      avg_net * 12,
    }

    return {
        "months": months,
        "rev_total": rev_total,
        "exp_total": exp_total,
        "customers": customer_rows,
        "expenses":  expense_rows,
        "customer_concentration": {"hhi": cust_hhi, "top1": cust_top1, "top3": cust_top3},
        "expense_concentration":  {"hhi": exp_hhi,  "top1": exp_top1,  "top3": exp_top3},
        "mom": mom_lines,
        "volatility": vol_lines,
        "anomalies": anomalies,
        "duplicates_to_review": dup_groups,
        "employees": {"count_by_inn": len(employee_inns), "names": sorted(employee_names)},
        "burn_per_month": burn_per_month,
        "runway_months": runway_months,
        "opening_balance": opening_balance,
        "closing_balance": closing_balance,
        "annualised": annual,
        "avg_per_month": {
            "revenue": avg_rev, "cogs": avg_cogs, "opex": avg_opex,
            "tax": avg_tax, "other": avg_oth, "net": avg_net,
        },
    }


def load_categorized(path: Path = Path("reports/categorized.csv")) -> list[dict]:
    return list(csv.DictReader(path.open(encoding="utf-8")))


if __name__ == "__main__":
    a = compute(load_categorized(),
                opening_balance=Decimal("3869.02"),
                closing_balance=Decimal("9568.09"))
    print(f"Months: {a['months']}")
    print(f"Customer concentration: HHI={a['customer_concentration']['hhi']:.0f}  "
          f"top-1={a['customer_concentration']['top1']:.1f}%  top-3={a['customer_concentration']['top3']:.1f}%")
    print(f"Expense  concentration: HHI={a['expense_concentration']['hhi']:.0f}  "
          f"top-1={a['expense_concentration']['top1']:.1f}%  top-3={a['expense_concentration']['top3']:.1f}%")
    print(f"Employees (by INN): {a['employees']['count_by_inn']}")
    print(f"Burn / month: {a['burn_per_month']:,.2f}  Runway: "
          f"{a['runway_months']:.2f} months" if a['runway_months'] else "  Runway: ∞")
    print(f"Anomalous (CV>1) categories: {len(a['anomalies'])}")
    print(f"Same-day duplicate-looking pairs: {len(a['duplicates_to_review'])}")
