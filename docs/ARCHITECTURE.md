# Architecture

This repo is two things in one tree:

1. A **workspace setup** for Claude Code on the web (Superpowers + Beads +
   Template Bridge + 3 MCP servers).
2. A **finance pipeline** that turns a Russian bank statement (XLSX) into a
   cash-basis Profit & Loss report (CSV + Markdown + executive PDF).

## Directory layout

```
Andrey-core/
├── CLAUDE.md            # primary brief loaded by Claude Code (project-level)
├── AGENTS.md            # equivalent brief for non-Claude agents (Codex etc.)
├── README.md            # human-facing overview
├── SETUP.md             # how to provision the workspace locally
├── LICENSE              # MIT
├── .editorconfig        # whitespace/encoding convention
├── .gitignore           # excludes data/*.xlsx, .charts/, *.db, .dolt/
│
├── docs/                # documentation root (this folder)
│   ├── AGENT.md         # Andrey's persona + operating principles
│   ├── ARCHITECTURE.md  # this file
│   ├── CONVENTIONS.md   # code & process conventions
│   └── MEMORY.md        # how memory is persisted (CLAUDE.md + bd remember + docs/)
│
├── finance/             # the P&L pipeline (Python stdlib + reportlab + matplotlib)
│   ├── parse.py         # XLSX → reports/operations.csv
│   ├── categorize.py    # operations.csv + rules.json → reports/categorized.csv
│   ├── rules.json       # by_inn / by_keyword / default rules
│   ├── analytics.py     # concentration (HHI), MoM, runway, anomalies, top-N
│   ├── pnl.py           # categorized.csv → pnl.{csv,md,pdf}  (multi-page exec PDF)
│   ├── run.sh           # end-to-end orchestrator
│   └── README.md        # pipeline-specific notes
│
├── reports/             # committed pipeline outputs (PII-aware)
│   ├── operations.csv   # all parsed rows
│   ├── categorized.csv  # + side, category, vat_amount, net_amount
│   ├── pnl.csv          # P&L matrix
│   ├── pnl.md           # Markdown P&L
│   ├── pnl.pdf          # 8-page executive PDF (RU)
│   └── .charts/         # generated PNGs (gitignored)
│
├── data/                # private inputs — gitignored
│   └── statement_*.xlsx
│
├── setup.sh             # provisioner for ~/.claude (plugins + hooks + CLAUDE.md)
│
├── .claude/             # Claude-specific project state
│   ├── settings.json    # project hooks (currently empty — see ~/.claude/settings.json)
│   └── agents/          # specialised sub-agents (e.g. security-auditor)
│
└── .beads/              # beads issue tracker (Dolt-backed DB + JSONL export)
    ├── issues.jsonl     # passive export (committed, human-readable)
    └── beads.db         # primary store (gitignored)
```

## Data flow

```
data/statement_2026-01_2026-04.xlsx
        │
        │  finance/parse.py
        ▼
reports/operations.csv     (157 rows, all of bank's "Операции" sheet)
        │
        │  finance/categorize.py (uses finance/rules.json)
        ▼
reports/categorized.csv    (+ side / category / vat_amount / net_amount)
        │
        │  finance/analytics.py + finance/pnl.py
        ▼
reports/pnl.csv  reports/pnl.md  reports/pnl.pdf
```

`run.sh` chains the three scripts; each is idempotent and deterministic.

## Conventions for new code in finance/

- **Python 3.11+, stdlib only** where possible. The only third-party deps
  are `reportlab` (PDF) and `matplotlib` (charts). Adding new deps requires
  a deliberate decision documented in the commit body.
- **`Decimal` for amounts.** Never `float`.
- **Cell access by `r` attribute** for XLSX parsing — `parse.py` shows the
  pattern.
- **Russian for human-visible strings**, English for identifiers / log
  messages / commit subjects.
- **Determinism.** Sort rows by `(op_date, doc_no, counterparty)` before
  writing CSV so re-runs diff cleanly.

## Conventions for the workspace setup

See `SETUP.md` for the user-facing version. The short of it: `setup.sh` is
idempotent, uses `jq` to merge hooks additively into `~/.claude/settings.json`,
and treats the cloned template-bridge as the canonical source of the global
CLAUDE.md workflow block.
