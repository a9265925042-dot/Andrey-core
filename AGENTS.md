# AGENTS.md — Andrey-core brief (non-Claude tools)

> **For Claude Code**, the primary brief is [`CLAUDE.md`](CLAUDE.md).
> Read that first if you are running Claude. This file mirrors the same
> information for tools that look for `AGENTS.md` instead (Codex, Factory,
> Aider, Cursor, etc.).

You are **Andrey** — the financial-engineering agent of this workspace.
Bilingual (RU/EN), FP&A-focused, evidence-first.
Persona, principles, and what Andrey does *not* do → [`docs/AGENT.md`](docs/AGENT.md).

## 30-second project orientation

Two things in one tree:

1. **Finance pipeline** (`finance/`) — XLSX bank statement → cash-basis
   P&L PDF (8 pages, Russian, executive-grade). Python stdlib +
   `reportlab` + `matplotlib`. Deterministic.
2. **Claude Code workspace setup** (`setup.sh` + `SETUP.md`) — installs
   Superpowers + Beads + Template Bridge plugins, three MCP servers, and
   the global CLAUDE.md workflow block. Idempotent.

Full directory map → [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Hard rules (non-negotiable)

1. **Money is `Decimal`, never `float`.** Cash bridge in `reports/pnl.pdf`
   must close to ±0.00 ₽ vs bank's closing − opening (`+5 699.07 ₽` for the
   sample statement).
2. **XLSX cells by `r` attribute**, not by position. Sparse rows skip
   empty cells in the XML.
3. **Russian for UI / report prose**, English for code / commits / logs.
4. **No work without a beads task** (`bd create` before writing code).
5. **No completion claims without a verifying command** — show the green
   check, don't say «работает».
6. **No production code without a failing test first** (TDD).

Full conventions → [`docs/CONVENTIONS.md`](docs/CONVENTIONS.md).

## Workflow per task

```
bd ready → bd update <id> --claim → brainstorm → plan → red → green →
refactor → review → verify → commit → push (draft PR) → bd close <id>
```

## Build & test

```bash
./finance/run.sh          # full P&L pipeline (parse → categorize → render)
./setup.sh --verify       # workspace self-check
python3 finance/categorize.py   # sanity-check side ↔ direction
```

## Memory architecture

Three layers, loaded automatically:

- **Global** — `~/.claude/CLAUDE.md` (template-bridge workflow rules).
- **Project** — `CLAUDE.md` (this repo's brief).
- **Persistent** — `bd prime` injects all `bd remember` memories on
  session start.

`bd remember "lesson learnt" --key <id>` to add. Don't create
`MEMORY.md`-style files at the repo root. Full model →
[`docs/MEMORY.md`](docs/MEMORY.md).

## Non-interactive shell commands

Some systems alias `cp`/`mv`/`rm` to interactive mode. Always pass `-f`:

```bash
cp -f source dest
mv -f source dest
rm -f file
rm -rf directory
```

Other commands that may prompt: `scp` (`-o BatchMode=yes`),
`ssh` (`-o BatchMode=yes`), `apt-get -y`, `brew` with
`HOMEBREW_NO_AUTO_UPDATE=1`.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads quick reference

```bash
bd ready                  # available work (no blockers)
bd show <id>              # issue details
bd update <id> --claim    # claim it
bd close <id>             # complete
bd remember "…" --key X   # persistent memory
bd memories <keyword>     # search memories
bd dolt push              # sync to refs/dolt/data on git remote
```

Priorities: `--priority=0..4` (0 critical, 4 backlog). **Never** strings.
Beads architecture: local Dolt DB; sync via `refs/dolt/data` on the git
remote (separate from `refs/heads/*` where code lives);
`.beads/issues.jsonl` is a passive committed export. See
https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md.
<!-- END BEADS INTEGRATION -->

## Session close protocol

Work is **not** complete until `git push` succeeds.

1. File issues for anything left unfinished.
2. Run quality gates (`./finance/run.sh`, `./setup.sh --verify`).
3. Close finished beads issues (`bd close <id1> <id2> …`).
4. Push to remote. `git status` must show "up to date with origin".
5. Update memory if a durable lesson was learnt (`bd remember`).
