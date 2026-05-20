# Project Instructions for AI Agents

This file provides instructions and context for AI coding agents working on this project.

<!-- BEGIN BEADS INTEGRATION v:1 profile:minimal hash:7510c1e2 -->
## Beads Issue Tracker

This project uses **bd (beads)** for issue tracking. Run `bd prime` to see full workflow context and commands.

### Quick Reference

```bash
bd ready              # Find available work
bd show <id>          # View issue details
bd update <id> --claim  # Claim work
bd close <id>         # Complete work
```

### Rules

- Use `bd` for ALL task tracking — do NOT use TodoWrite, TaskCreate, or markdown TODO lists
- Run `bd prime` for detailed command reference and session close protocol
- Use `bd remember` for persistent knowledge — do NOT use MEMORY.md files

**Architecture in one line:** issues live in a local Dolt DB; sync uses `refs/dolt/data` on your git remote; `.beads/issues.jsonl` is a passive export. See https://github.com/gastownhall/beads/blob/main/docs/SYNC_CONCEPTS.md for details and anti-patterns.

## Session Completion

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
<!-- END BEADS INTEGRATION -->

## Workflow: Superpowers + Beads + Templates

Before starting ANY task, invoke `template-bridge:unified-workflow` skill to load the full workflow.

### Quick Reference (do NOT skip steps)

1. **Epic** — `bd create -t epic "Goal"` (container for intent + context)
2. **Brainstorm** — `superpowers:brainstorming` (design before code)
3. **Plan** — `superpowers:writing-plans` (2-5 min tasks)
4. **Sub-tasks** — `bd create` for each + `bd dep add` (parent-child, blocks)
5. **Isolate** — `superpowers:using-git-worktrees` (non-trivial work)
6. **Implement** — `bd ready` → pick → `bd update --claim` → TDD (RED → GREEN → REFACTOR)
7. **Review** — `superpowers:requesting-code-review`
8. **Verify** — `superpowers:verification-before-completion` (evidence before claims)
9. **Finish** — `superpowers:finishing-a-development-branch`
10. **Close** — `bd close <epic-id> --reason "Done"`

### Rules

- No production code without a failing test first
- No completion claims without running verification commands
- No work without a beads task
- **Always query Context7 before implementing with any library/framework** (`resolve-library-id` → `query-docs`)
- Check `template-bridge:template-catalog` when a specialist agent is needed
- Side quests: `bd create -t bug` + `bd dep add new current --type discovered-from`

## Build & Test

_Add your build and test commands here_

```bash
# Example:
# npm install
# npm test
```

## Architecture Overview

_Add a brief overview of your project architecture_

## Conventions & Patterns

_Add your project-specific conventions here_
