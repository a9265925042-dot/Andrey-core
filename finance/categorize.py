#!/usr/bin/env python3
"""
categorize.py — apply rules.json to operations.csv, producing categorized.csv.

Adds columns: side, category, vat_rate, vat_amount, net_amount, name_hint, sign.
- `sign`: +1 for inflow (credit), -1 for outflow (debit).
- `vat_amount`: parsed from the purpose text (`в т.ч. НДС N% xxx`) if present;
  otherwise computed from vat_rate.
- `net_amount`: gross - vat, where gross = debit or credit.

Self-account transfers (matching `self_inn`) are tagged `internal_transfer` and
excluded from P&L aggregation downstream.

Usage:
    python3 categorize.py [--in reports/operations.csv] [--rules finance/rules.json] [--out reports/categorized.csv]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

NDS_RE = re.compile(r"НДС[^0-9%]*(\d{1,2})\s*%[^0-9]*([0-9][\d\s.,]*)", re.IGNORECASE)


def load_rules(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def to_dec(s: str) -> Decimal:
    s = (s or "").strip().replace(" ", "").replace("\xa0", "").replace(",", ".")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except Exception:
        return Decimal("0")


def extract_vat_from_purpose(purpose: str) -> tuple[Decimal | None, int | None]:
    """Return (vat_amount, vat_rate) if found in purpose; else (None, None)."""
    if not purpose:
        return None, None
    m = NDS_RE.search(purpose)
    if not m:
        return None, None
    rate = int(m.group(1))
    raw = m.group(2).replace(" ", "").replace("\xa0", "").replace(",", ".")
    # Strip trailing punctuation that may have been captured.
    raw = re.sub(r"[^\d.]+$", "", raw)
    if raw.count(".") > 1:
        # Russian style "1.234.56" → keep last dot as decimal.
        head, _, tail = raw.rpartition(".")
        raw = head.replace(".", "") + "." + tail
    try:
        return Decimal(raw), rate
    except Exception:
        return None, rate


def classify(rec: dict, rules: dict) -> dict:
    inn = (rec.get("inn") or "").strip()
    purpose = rec.get("purpose") or ""
    self_inn = rules.get("self_inn", "")

    rule = None
    if inn and inn == self_inn:
        rule = {"side": "internal_transfer", "category": "internal_transfer", "vat_rate": 0, "name_hint": "self transfer"}
    elif inn and inn in rules["by_inn"]:
        rule = rules["by_inn"][inn]
    else:
        for kw in rules.get("by_keyword", []):
            if re.search(kw["match"], purpose):
                rule = kw
                break
    if rule is None:
        rule = dict(rules.get("default", {"side": "opex", "category": "opex.other", "vat_rate": 0}))

    return rule


def categorize(rows: list[dict], rules: dict) -> list[dict]:
    out: list[dict] = []
    for r in rows:
        rule = classify(r, rules)
        debit = to_dec(r.get("debit", ""))
        credit = to_dec(r.get("credit", ""))
        if debit > 0 and credit > 0:
            # Should not happen — bank either debits or credits, not both.
            pass
        sign = 1 if credit > 0 else -1
        gross = credit if credit > 0 else debit

        vat_amt, vat_rate_in_purpose = extract_vat_from_purpose(r.get("purpose", ""))
        rule_rate = int(rule.get("vat_rate", 0) or 0)
        # If purpose explicitly says VAT 0 / "НДС не облагается", prefer that.
        if "не облагается" in (r.get("purpose") or "").lower() or "без ндс" in (r.get("purpose") or "").lower():
            vat_amt = Decimal("0")
            vat_rate_in_purpose = 0
        if vat_amt is None and rule_rate > 0:
            # Compute from rate: VAT = gross * rate / (100 + rate).
            vat_amt = (gross * Decimal(rule_rate) / Decimal(100 + rule_rate)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if vat_amt is None:
            vat_amt = Decimal("0")

        # Internal transfers and tax payments: zero VAT.
        if rule["side"] in {"internal_transfer", "tax"}:
            vat_amt = Decimal("0")
            rule_rate = 0
        rate_out = vat_rate_in_purpose if vat_rate_in_purpose is not None else rule_rate

        net = (gross - vat_amt).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        out.append({
            **r,
            "side": rule["side"],
            "category": rule["category"],
            "name_hint": rule.get("name_hint", ""),
            "vat_rate": rate_out,
            "vat_amount": f"{vat_amt:.2f}",
            "net_amount": f"{net:.2f}",
            "sign": sign,
        })
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--in",   dest="inp",   type=Path, default=Path("reports/operations.csv"))
    p.add_argument("--rules", type=Path, default=Path("finance/rules.json"))
    p.add_argument("--out",   type=Path, default=Path("reports/categorized.csv"))
    args = p.parse_args()

    rows = list(csv.DictReader(args.inp.open(encoding="utf-8")))
    rules = load_rules(args.rules)
    out_rows = categorize(rows, rules)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) + ["side", "category", "name_hint", "vat_rate", "vat_amount", "net_amount", "sign"]
    with args.out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(out_rows)

    # Quick coverage stats.
    from collections import Counter
    cats = Counter(r["category"] for r in out_rows)
    sides = Counter(r["side"] for r in out_rows)
    other = [r for r in out_rows if r["category"] == "opex.other"]
    print(f"categorized {len(out_rows)} rows → {args.out}")
    print(f"  sides: {dict(sides)}")
    print(f"  unique categories: {len(cats)}")
    print(f"  opex.other (review): {len(other)}")
    for r in other:
        print(f"    - {r.get('op_date','')}  inn={r.get('inn','')}  {r.get('counterparty','')[:40]}  {r.get('debit') or r.get('credit')}")

    # Sanity: side must match cash direction.
    #   revenue / other_income / internal_transfer(credit) → credit > 0
    #   cogs / opex / tax / internal_transfer(debit)        → debit  > 0
    bad: list[str] = []
    for r in out_rows:
        side = r["side"]
        d = to_dec(r["debit"])
        c = to_dec(r["credit"])
        if side in ("revenue", "other_income") and c == 0:
            bad.append(f"    ! {r['op_date']} {side} but debit={d} credit=0 — {r['counterparty'][:40]} (INN {r['inn']})")
        elif side in ("cogs", "opex", "tax") and d == 0:
            bad.append(f"    ! {r['op_date']} {side} but credit={c} debit=0 — {r['counterparty'][:40]} (INN {r['inn']})")
    if bad:
        print(f"  ⚠ direction-side mismatches: {len(bad)}")
        for line in bad[:25]:
            print(line)
        if len(bad) > 25:
            print(f"    … and {len(bad) - 25} more")
    else:
        print("  ✓ all rows: side matches cash direction")
    return 0


if __name__ == "__main__":
    sys.exit(main())
