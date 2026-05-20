# Conventions

Read this once. After that, follow it without thinking.

## Code

### Python

- **Version**: 3.11+ (`Decimal`, `match`/`case`, PEP 604 unions).
- **Style**: PEP 8 with type hints on every public function. Two-space tab
  *never* — four spaces only.
- **Imports**: stdlib → third-party → local, separated by blank lines.
  `from __future__ import annotations` at the top of every module.
- **Money**: `decimal.Decimal` only. `quantize(Decimal("0.01"),
  rounding=ROUND_HALF_UP)` at the final formatting step. Never `float()`
  monetary values except inside chart rendering helpers (matplotlib needs
  float).
- **Errors**: fail loudly. Don't swallow exceptions. `raise SystemExit`
  with a clear message at CLI boundaries; let everything else propagate.
- **Determinism**: explicit sort before any output. Hash of stdout should
  be stable across runs of the same input.

### XLSX parsing

- Open via `zipfile` + `xml.etree.ElementTree`. No `openpyxl`/`pandas`
  unless explicitly justified.
- Address cells by `r` attribute (`re.match(r'^([A-Z]+)', addr)`), never by
  position. Sparse rows skip empty cells in the XML.
- Excel dates are days since `1899-12-30`. Convert with
  `EXCEL_EPOCH + timedelta(days=int(serial))`.

### Reports

- Russian text for everything the user reads (PDF labels, table headers,
  Markdown narrative).
- English for code, identifiers, log lines, commit messages.
- Numbers: NBSP (` `) as thousands separator so reportlab does not
  wrap mid-number. Parentheses for negatives, not minus signs.
- Currency symbol `₽` rendered with DejaVu Sans (registered via
  `_register_cyrillic_fonts()` in `pnl.py`); fallback to Helvetica if absent.

## Process

### Workflow per task

```
1. bd create -t feature|bug|task --title "…"     (or pick from bd ready)
2. bd update <id> --claim
3. brainstorm (superpowers:brainstorming) if not trivial
4. plan (superpowers:writing-plans) — 2–5 minute tasks
5. red / green / refactor for each task
6. superpowers:requesting-code-review
7. superpowers:verification-before-completion — run the proving command
8. commit + push (draft PR if user-facing)
9. bd close <id> --reason "…"
```

### Beads (`bd`)

- Use for **all** task tracking. No TodoWrite, no markdown TODO lists.
- Priorities: P0 critical → P4 backlog. Never strings.
- One issue per shippable unit. Sub-issues for sub-tasks. Use `bd dep add`.
- Use `bd remember "fact"` for cross-session knowledge. See `docs/MEMORY.md`.

### Git

- Branch per feature: `claude/<descriptor>-<context>` (e.g.
  `claude/pnl-2026-q1`).
- Commit subject ≤ 70 chars, imperative mood, English.
  Body wraps at 80 — explains *why*, not *what*. Include verification
  evidence (output of the proving command, sums that close, etc.).
- One logical change per commit. Refactor commits separate from feature
  commits.
- Draft PRs only — promote to ready only after the user signs off.
- Never force-push to `main`. Never `--no-verify` without explicit user ask.

### Commits Andrey aspires to

```
Executive-grade Russian P&L: exec summary, concentration, waterfall

Data audit: row-by-row XLSX ↔ operations.csv reconciliation. 158 source
rows → 157 data rows + 1 blank trailing row correctly excluded by parser.
Gross debit / credit sums match bank statement to the kopeck.

…
```

vs commits Andrey does not write:

```
fix stuff
WIP
update
```

## Privacy

- `data/*.xlsx` and any other bank statement format → `.gitignore`d.
  *Never* commit a real statement, even to a private repo, without
  user explicit consent.
- `reports/*.csv` and `pnl.pdf` contain PII (counterparty names, INNs,
  account numbers). The repo is assumed **private**. README warns about
  this.

## Style notes for the human-facing prose

- Use «французские ёлочки» for Russian quotes. `"American quotes"` only
  inside code blocks.
- Long em-dash `—` between clauses, not `--`.
- No emojis unless the user asks. Status symbols (`✓ ⚠ ✗`) only in CLI
  output, never in committed Markdown.
- Numbers in narrative prose: `3 257 677.32 ₽` with NBSPs.
