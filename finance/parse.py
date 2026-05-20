#!/usr/bin/env python3
"""
parse.py — Read a Russian bank statement XLSX and emit a normalised CSV.

Designed for stdlib only (zipfile + xml.etree). Reads cells by their `r`
attribute (e.g. "K3" → column K) so it survives sparse rows where Excel
omits empty cells.

Usage:
    python3 parse.py <path/to/statement.xlsx> [--out reports/operations.csv]
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import re
import sys
import zipfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree import ElementTree as ET

NS = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
SHEETML = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

# 1899-12-30 is the Excel epoch (Lotus 1900 leap-year bug compensation).
EXCEL_EPOCH = dt.date(1899, 12, 30)

# Header → CSV column name. Russian headers from the bank's statement.
HEADER_MAP = {
    "Номер документа": "doc_no",
    "Дата документа": "doc_date",
    "Дата операции": "op_date",
    "Счёт": "account",
    "Контрагент": "counterparty",
    "ИНН контрагента": "inn",
    "БИК банка контрагента": "bik",
    "Корр.счёт банка контрагента": "corr_acct",
    "Наименование банка контрагента": "bank_name",
    "Счёт контрагента": "cp_account",
    "Списание": "debit",
    "Зачисление": "credit",
    "Назначение платежа": "purpose",
}

CSV_COLS = [
    "doc_no", "doc_date", "op_date", "account",
    "counterparty", "inn", "bik", "corr_acct", "bank_name", "cp_account",
    "debit", "credit", "purpose",
]


def col_to_idx(col: str) -> int:
    """'A' -> 0, 'Z' -> 25, 'AA' -> 26."""
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch.upper()) - ord("A") + 1)
    return n - 1


def cell_addr_to_col(addr: str) -> int:
    m = re.match(r"^([A-Z]+)\d+$", addr)
    if not m:
        raise ValueError(f"Bad cell address: {addr}")
    return col_to_idx(m.group(1))


def load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root.findall(f"{SHEETML}si"):
        # Concatenate all <t> within the <si> (handles rich text with multiple runs).
        out.append("".join((t.text or "") for t in si.iter(f"{SHEETML}t")))
    return out


def cell_value(c: ET.Element, strings: list[str]) -> str:
    t = c.attrib.get("t")
    v_el = c.find(f"{SHEETML}v")
    if t == "s":
        if v_el is None:
            return ""
        try:
            return strings[int(v_el.text or "0")]
        except (IndexError, ValueError):
            return v_el.text or ""
    if t == "inlineStr":
        return "".join((x.text or "") for x in c.iter(f"{SHEETML}t"))
    if v_el is None:
        return ""
    return v_el.text or ""


def row_to_dict(row: ET.Element, strings: list[str], headers: list[str]) -> dict[str, str]:
    """Build {column-name: value} using cell `r` attribute (sparse-safe)."""
    out: dict[str, str] = {h: "" for h in headers}
    for c in row.findall(f"{SHEETML}c"):
        addr = c.attrib.get("r", "")
        if not addr:
            continue
        idx = cell_addr_to_col(addr)
        if 0 <= idx < len(headers):
            out[headers[idx]] = cell_value(c, strings).strip()
    return out


def find_operations_sheet(zf: zipfile.ZipFile) -> str:
    """Locate the sheet whose first data row contains operation headers."""
    workbook = ET.fromstring(zf.read("xl/workbook.xml"))
    rels = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {
        r.attrib["Id"]: r.attrib["Target"]
        for r in rels.findall("{http://schemas.openxmlformats.org/package/2006/relationships}Relationship")
    }
    candidates: list[str] = []
    for sheet in workbook.find(f"{SHEETML}sheets").findall(f"{SHEETML}sheet"):
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = rid_to_target[rid]
        if not target.startswith("xl/"):
            target = "xl/" + target
        candidates.append(target)
    # Prefer the sheet whose first non-empty row matches our header set.
    strings = load_shared_strings(zf)
    for target in candidates:
        root = ET.fromstring(zf.read(target))
        for row in root.iter(f"{SHEETML}row"):
            vals = [cell_value(c, strings).strip() for c in row.findall(f"{SHEETML}c")]
            joined = " | ".join(vals)
            if "Номер документа" in joined and "Списание" in joined and "Зачисление" in joined:
                return target
            # First non-empty row only.
            if any(vals):
                break
    raise SystemExit("Could not find the 'Операции' sheet with expected headers.")


def parse_amount(s: str) -> Decimal:
    s = (s or "").strip().replace(",", ".").replace("\xa0", "").replace(" ", "")
    if not s:
        return Decimal("0")
    try:
        return Decimal(s)
    except InvalidOperation:
        return Decimal("0")


def excel_serial_to_iso(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    try:
        serial = float(s)
    except ValueError:
        # Sometimes already a date string — return as-is.
        return s
    days = int(serial)
    date = EXCEL_EPOCH + dt.timedelta(days=days)
    return date.isoformat()


def parse_xlsx(path: Path) -> list[dict[str, str]]:
    with zipfile.ZipFile(path) as zf:
        strings = load_shared_strings(zf)
        sheet = find_operations_sheet(zf)
        root = ET.fromstring(zf.read(sheet))

        rows = list(root.iter(f"{SHEETML}row"))

    # Locate header row (first row whose first cell contains "Номер документа")
    header_row_idx = None
    headers_by_col: list[str] = []
    for i, row in enumerate(rows):
        cells = row.findall(f"{SHEETML}c")
        # Build header by addressing each cell to its column index.
        proposed = [""] * 30
        for c in cells:
            addr = c.attrib.get("r", "")
            if not addr:
                continue
            idx = cell_addr_to_col(addr)
            val = cell_value(c, strings).strip()
            if idx < len(proposed):
                proposed[idx] = val
        if "Номер документа" in proposed and "Списание" in proposed:
            headers_by_col = [HEADER_MAP.get(v, "") for v in proposed]
            header_row_idx = i
            break
    if header_row_idx is None:
        raise SystemExit("Header row not found on operations sheet.")

    records: list[dict[str, str]] = []
    for row in rows[header_row_idx + 1:]:
        rec = row_to_dict(row, strings, headers_by_col)
        # Skip rows with no real content (no inn AND no amounts AND no purpose).
        if not (rec.get("inn") or rec.get("debit") or rec.get("credit") or rec.get("purpose")):
            continue
        # Normalise.
        rec["doc_date"] = excel_serial_to_iso(rec.get("doc_date", ""))
        rec["op_date"] = excel_serial_to_iso(rec.get("op_date", ""))
        rec["debit"] = str(parse_amount(rec.get("debit", ""))) if rec.get("debit") else ""
        rec["credit"] = str(parse_amount(rec.get("credit", ""))) if rec.get("credit") else ""
        rec["counterparty"] = (rec.get("counterparty", "") or "").replace("\n", " ").strip()
        rec["purpose"] = (rec.get("purpose", "") or "").replace("\n", " ").strip()
        records.append(rec)
    return records


def write_csv(records: list[dict[str, str]], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Deterministic order: by op_date asc, then doc_no, then counterparty.
    records = sorted(records, key=lambda r: (r.get("op_date", ""), r.get("doc_no", ""), r.get("counterparty", "")))
    with out.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLS, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("xlsx", type=Path)
    p.add_argument("--out", type=Path, default=Path("reports/operations.csv"))
    args = p.parse_args()
    if not args.xlsx.exists():
        print(f"File not found: {args.xlsx}", file=sys.stderr)
        return 2
    records = parse_xlsx(args.xlsx)
    write_csv(records, args.out)

    debit_total = sum(Decimal(r["debit"] or "0") for r in records)
    credit_total = sum(Decimal(r["credit"] or "0") for r in records)
    print(f"parsed {len(records)} rows → {args.out}")
    print(f"  total debit  (списание):   {debit_total:>16,.2f}")
    print(f"  total credit (зачисление): {credit_total:>16,.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
