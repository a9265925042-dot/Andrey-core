#!/usr/bin/env bash
# Build the P&L pipeline end-to-end:
#   parse.py → categorize.py → pnl.py (CSV + MD + PDF)
#
# Usage:
#   ./finance/run.sh <path-to-statement.xlsx>
#   ./finance/run.sh           # uses data/statement_*.xlsx if exactly one matches

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

XLSX="${1:-}"
if [[ -z "$XLSX" ]]; then
  matches=("$REPO_ROOT"/data/statement_*.xlsx)
  if [[ ${#matches[@]} -ne 1 || ! -f "${matches[0]}" ]]; then
    echo "usage: $0 <path-to-statement.xlsx>" >&2
    exit 2
  fi
  XLSX="${matches[0]}"
fi

cd "$REPO_ROOT"
echo "[1/3] parse.py        ← $XLSX"
python3 finance/parse.py "$XLSX"
echo "[2/3] categorize.py"
python3 finance/categorize.py
echo "[3/3] pnl.py          → reports/pnl.{csv,md,pdf}"
python3 finance/pnl.py
echo "Done."
