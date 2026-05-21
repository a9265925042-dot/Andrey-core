# Andrey-core

Financial-engineering workspace for Claude Code: a cash-basis P&L pipeline for
Russian SMEs, plus the Claude Code provisioning that runs it.

## What's inside

| Path | Purpose |
| --- | --- |
| [`finance/`](finance/) | XLSX bank statement → executive PDF P&L pipeline |
| [`reports/`](reports/) | Committed outputs of the pipeline (CSV / MD / PDF) |
| [`data/`](data/) | Private bank statements — gitignored |
| [`setup.sh`](setup.sh) · [`SETUP.md`](SETUP.md) | Workspace provisioner (plugins + hooks + MCP servers) |
| [`docs/`](docs/) | Architecture, conventions, agent persona, memory model |

## Quick start

```bash
# 1. Provision a Claude Code workspace (one-time)
./setup.sh                # installs Superpowers + Beads + Template Bridge,
                          # adds 3 MCP servers, sets up hooks. Idempotent.
./setup.sh --verify       # health check, no changes

# 2. Drop a bank statement into data/, then run the pipeline
cp ~/path/to/statement.xlsx data/statement_2026-01_2026-04.xlsx
./finance/run.sh

# 3. Open reports/pnl.pdf — 8-page executive P&L (Russian)
```

## The P&L report

`reports/pnl.pdf` is an 8-page executive-grade P&L:

1. **Сводка для руководителя** — RAG status + headline KPIs + Top-3
   drivers/risks/actions, plus a data-integrity check.
2. **KPI grid + Отчёт об операциях** — 12 KPI tiles incl. burn rate,
   runway, Top-1 customer share, HHI; classic monthly P&L with a
   `% Выр` (common-size) column.
3. **Сверка с банком** — cash bridge from net P&L (without VAT) to the
   bank's closing − opening. Must close to **0.00 ₽**.
4. **Мост EBIT** — waterfall chart first month → last month.
5. **Концентрация — Клиенты** — Pareto chart + HHI + Top-N table.
6. **Концентрация — Расходы** — Pareto chart + HHI + Top-15 table.
7. **Динамика и структура** — Monthly trend lines + expense bar chart.
8. **Методология / Примечания**.

The pipeline is **deterministic**: same XLSX in → byte-identical CSV out.

## Documentation

| Doc | Read it when… |
| --- | --- |
| [`docs/AGENT.md`](docs/AGENT.md) | … you need to know the agent's voice, principles, and what it won't do |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | … you need the directory map and data flow |
| [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md) | … you're writing code or commits |
| [`docs/MEMORY.md`](docs/MEMORY.md) | … you need to understand how state survives between sessions |

## License

[MIT](LICENSE). The setup scripts use MIT-licensed material from
[Superpowers](https://github.com/obra/superpowers),
[Beads](https://github.com/steveyegge/beads),
[Template Bridge](https://github.com/maslennikov-ig/template-bridge), and
[claude-code-templates](https://github.com/davila7/claude-code-templates),
with attribution in their respective sections.
