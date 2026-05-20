# Memory architecture

Andrey runs in ephemeral Claude Code sessions. Each session starts with no
in-process memory of the previous one. Persistence is layered on top.

## Three layers, in load order

```
┌──────────────────────────────────────────────────────────────┐
│ 1. ~/.claude/CLAUDE.md      ← global, loaded for every repo  │
│    template-bridge workflow + universal rules                │
├──────────────────────────────────────────────────────────────┤
│ 2. ./CLAUDE.md              ← project-level, loaded for this │
│    Andrey persona pointer + key project facts + workflow     │
├──────────────────────────────────────────────────────────────┤
│ 3. bd prime  (SessionStart hook)                             │
│    Beads-persisted memories — the "lessons learnt" layer     │
└──────────────────────────────────────────────────────────────┘
```

After those three resolve, Andrey has:
- the persona (`docs/AGENT.md`)
- the architecture (`docs/ARCHITECTURE.md`)
- the conventions (`docs/CONVENTIONS.md`)
- this memory map (`docs/MEMORY.md`)
- the workflow (`CLAUDE.md` + template-bridge)
- the beads issue board (`bd ready` / `bd list`)
- the persistent memories (`bd memories`)

That is sufficient to resume any in-flight task.

## What goes where

| Type of knowledge | Lives in |
| --- | --- |
| Workflow rules (TDD, review, verify, finish) | `~/.claude/CLAUDE.md` (template-bridge) |
| Project facts (what this repo is, who Andrey is) | `./CLAUDE.md` |
| Long-form architecture / persona / conventions | `docs/*.md` |
| In-flight task state (open issues, dependencies) | `bd` (Dolt + JSONL) |
| Cross-session lessons ("this caused a bug last time") | `bd remember` |
| Per-task design notes | `bd update <id> --design` |
| Per-task running notes | `bd update <id> --notes` |
| Code-level invariants | docstrings + inline `# why` comments |

## `bd remember` usage

```bash
bd remember "amounts are Decimal — float breaks the cash-bridge math" --key money-decimal
bd remember "XLSX cells: address by r attribute, not position — sparse rows skip empty cells" --key xlsx-sparse
bd memories                   # list all
bd memories <keyword>         # search
```

Memories are auto-injected by the `SessionStart` and `PreCompact` hooks
configured in `~/.claude/settings.json` (`bd prime` calls them in).

Rules of thumb:
- A memory is **a lesson learnt from a bug**, not a definition. Definitions
  go in `docs/`. Lessons live in beads.
- Each memory has an explicit `--key` so it can be updated in place.
- Memories are short (one sentence). If it's longer, it's a doc.

## What's *not* memory

Files like `MEMORY.md` at the repo root, or `.ai/` directories, or chat
transcripts — these fragment across accounts and rot quickly. Don't create
them. The three layers above are sufficient.

## Refreshing memory after a major change

When something durable changes:

```bash
# 1. Update the right doc (AGENT/ARCHITECTURE/CONVENTIONS) or CLAUDE.md
$EDITOR docs/CONVENTIONS.md

# 2. If it's a lesson-learnt (vs a definition), also record it in beads
bd remember "the lesson, one sentence" --key short-id

# 3. Verify the next session can pick it up
bd prime    # should print the new memory
```
