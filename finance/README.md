# finance/ — bank statement → classic P&L

Pipeline that turns a Russian bank statement (XLSX) into a **cash-basis Profit & Loss**
report styled as a classic P&L. Outputs CSV, Markdown, and a print-ready PDF.

## Quick start

```bash
# Put the bank statement here (gitignored — keep PII out of git)
cp /path/to/bank-export.xlsx data/statement_2026-01_2026-04.xlsx

# Run the pipeline
./finance/run.sh

# View outputs
ls reports/
# operations.csv  categorized.csv  pnl.csv  pnl.md  pnl.pdf
```

## Pipeline

| Stage           | Script                  | Input                      | Output                       |
| --------------- | ----------------------- | -------------------------- | ---------------------------- |
| 1. Parse        | `finance/parse.py`      | `data/*.xlsx`              | `reports/operations.csv`     |
| 2. Categorize   | `finance/categorize.py` | `reports/operations.csv` + `finance/rules.json` | `reports/categorized.csv` |
| 3. Render P&L   | `finance/pnl.py` + `finance/excel.py` | `reports/categorized.csv`  | `reports/pnl.{csv,md,pdf,xlsx}`   |

Each script is independent and idempotent — re-running produces the same output bytes.

## Design

### Parser (`parse.py`)

- Pure stdlib (`zipfile` + `xml.etree.ElementTree`) — **no openpyxl / pandas**.
- Addresses cells by their `r` attribute (`K3` → column K = index 10) so it handles
  sparse XLSX rows where Excel omits empty cells. Naïve positional indexing breaks here.
- Excel serial dates → ISO via the standard `1899-12-30` epoch.

### Categorizer (`categorize.py`)

Rules are in `finance/rules.json`. Precedence:

1. **`self_inn`** — payments where counterparty INN equals the company's own INN are
   tagged `internal_transfer` and excluded from P&L (they're not income or expense).
2. **`by_inn`** — counterparty INN → side / category / VAT rate. Highest-priority match.
3. **`by_keyword`** — regex against the payment purpose. Ordered, first match wins.
4. **`default`** — falls through to `opex.other`.

VAT extraction: a row's purpose typically contains «в т.ч. НДС 20% 50000.00». The
categorizer parses that out; if absent and the rule has a positive `vat_rate`, VAT
is computed from rate (`gross × rate / (100+rate)`). Tax payments and internal
transfers are forced to zero VAT.

A sanity check at the end warns when a row's `side` doesn't match its cash direction
(e.g. a `revenue` row with zero credit) — that's how the «ЭЛИОН» mis-classification
was caught the first time around.

### P&L (`pnl.py`)

Classic structure, monthly columns + Total:

```
Revenue (net of VAT)
  marketplace_ozon / direct_b2b
Cost of goods sold (COGS)
  manufacturing
Gross profit                          + Gross margin %
Operating expenses
  Salaries / Transport / Shipping / Leasing / Insurance / Vehicle maintenance /
  Rent / Utilities / Telecom / Software / Accounting / Training / Bank fees / Other
Operating profit (EBIT)               + Operating margin %
Other income
Taxes (FNS + Social Fund)
  fns / social
Net cash result                       + Net margin %
```

Followed by a **Cash reconciliation** bridge from net P&L to actual bank cash change:

```
Net P&L result (net of VAT)
(+) VAT collected − VAT paid (timing lag)
=   External cash result
(+) Internal transfers from / to own accounts
=   Net bank cash change   ← must equal closing − opening from the bank
```

The PDF uses DejaVu Sans (Cyrillic + ₽), navy/gold/red palette, KPI tiles, a monthly
trend line chart, and an expense-structure horizontal bar chart.

## Adding / fixing categories

1. Run `python3 finance/categorize.py`. It prints a coverage summary and warns about
   `opex.other` rows that need a rule, plus any row whose side mismatches cash direction.
2. Open `finance/rules.json` and add either:
   - a new `by_inn` entry (preferred — most specific), or
   - a new `by_keyword` regex entry.
3. If you add a new category prefixed `opex.*`, also add it to `OPEX_GROUPS` in
   `finance/pnl.py` so it surfaces as its own P&L line. Otherwise its money will be
   silently dropped from the OpEx total — see commit history for the `opex.vehicle_maint`
   regression that motivated this note.
4. Re-run `./finance/run.sh`. Verify the **Cash reconciliation = Net bank cash change**
   line at the bottom of `reports/pnl.md` still matches the bank statement's
   `closing − opening` figure.

## Limitations (read this!)

- **Cash basis only.** This is a Profit &amp; Loss view of *cash movements*, not
  accrual accounting. Receivables / payables are invisible; inventory is invisible;
  capex is bundled into Manufacturing if you paid for it via the bank.
- **One account.** If the company has multiple bank accounts, this pipeline only
  sees one. Internal transfers between own accounts (matched by INN) are excluded,
  but real movement of business on other accounts is not.
- **Salaries are net of NDFL.** Personal income tax withheld at source goes through
  the ФНС line, not the Salaries line.
- **Rules are domain-specific.** The `rules.json` here was tuned for ООО «ТЕХСОЛ» 2026-Q1.
  For another company it needs new INNs (and possibly new categories).

## Files

```
finance/
├── README.md          # this file
├── parse.py           # XLSX → operations.csv
├── categorize.py      # rules.json + operations.csv → categorized.csv
├── pnl.py             # categorized.csv → pnl.{csv,md,pdf}
├── rules.json         # categorization rules
└── run.sh             # orchestrator

reports/
├── operations.csv     # all parsed rows
├── categorized.csv    # + side, category, vat_amount, net_amount
├── pnl.csv            # P&L matrix
├── pnl.md             # Markdown P&L + cash reconciliation
└── pnl.pdf            # print-ready PDF report

data/
└── *.xlsx             # source bank statements — gitignored
```
