#!/usr/bin/env bash
# Local workspace bootstrap — see SETUP.md for the full breakdown.
# Idempotent: safe to run multiple times.
#
# Usage:
#   ./setup.sh            # install everything
#   ./setup.sh --verify   # only run health checks, no changes
#
# Env vars:
#   SKIP_MCP=1            # don't register MCP servers (context7, sequential-thinking, playwright)
#   SKIP_BD_INIT=1        # don't run `bd init` in this repo
#   CLAUDE_HOME=...       # override ~/.claude location

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
TB_REPO_URL="https://github.com/maslennikov-ig/template-bridge.git"
TB_CACHE_DIR="$(mktemp -d -t template-bridge-XXXXXX)"
TMP_FILES=()

cleanup() {
  rm -rf "$TB_CACHE_DIR" 2>/dev/null || true
  for f in "${TMP_FILES[@]+"${TMP_FILES[@]}"}"; do
    rm -f "$f" 2>/dev/null || true
  done
}
trap cleanup EXIT

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ ok ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"; }

MODE="install"
[[ "${1:-}" == "--verify" ]] && MODE="verify"

###############################################################################
# Prerequisites
###############################################################################
require node
require npm
require npx
require git
require claude
command -v jq >/dev/null 2>&1 || fail "Missing jq (brew install jq | apt-get install jq)"

mkdir -p "$CLAUDE_HOME"

###############################################################################
# Helpers — marketplace / plugin presence checks (portable, no \s)
###############################################################################
marketplace_present() {
  # $1: GitHub repo slug, e.g. "obra/superpowers"
  claude plugin marketplace list 2>/dev/null | grep -qE "GitHub \(${1}\)\$"
}

plugin_present() {
  # $1: plugin short name, e.g. "superpowers"
  claude plugin list 2>/dev/null | grep -qE "^[[:space:]]*>[[:space:]]+${1}@"
}

mcp_present() {
  # $1: MCP server name
  claude mcp list 2>/dev/null | grep -qE "^${1}:[[:space:]]"
}

###############################################################################
# Verify mode — read-only
###############################################################################
if [[ "$MODE" == "verify" ]]; then
  log "Verifying workspace…"
  errors=0
  for r in obra/superpowers steveyegge/beads maslennikov-ig/template-bridge; do
    if marketplace_present "$r"; then ok "marketplace $r"; else warn "MISSING marketplace $r"; errors=$((errors+1)); fi
  done
  for p in superpowers beads template-bridge; do
    if plugin_present "$p"; then ok "plugin $p"; else warn "MISSING plugin $p"; errors=$((errors+1)); fi
  done
  if command -v bd >/dev/null 2>&1; then ok "bd $(bd --version | head -1)"; else warn "MISSING bd CLI"; errors=$((errors+1)); fi
  if grep -qF "## Workflow: Superpowers + Beads + Templates" "$CLAUDE_HOME/CLAUDE.md" 2>/dev/null; then
    ok "~/.claude/CLAUDE.md has workflow block"
  else
    warn "MISSING workflow block in ~/.claude/CLAUDE.md"; errors=$((errors+1))
  fi
  if [[ -f "$CLAUDE_HOME/settings.json" ]] && jq -e '
        ([(.hooks.SessionStart // [])[].hooks[]?.command] | any(. == "bd prime"))
        and ([(.hooks.PreCompact // [])[].hooks[]?.command]   | any(. == "bd prime"))
      ' "$CLAUDE_HOME/settings.json" >/dev/null; then
    ok "SessionStart + PreCompact hooks contain bd prime"
  else
    warn "MISSING bd prime hooks in $CLAUDE_HOME/settings.json"; errors=$((errors+1))
  fi
  if [[ -f "$REPO_ROOT/.claude/agents/security-auditor.md" ]]; then
    ok "security-auditor agent present"
  else
    warn "MISSING .claude/agents/security-auditor.md"; errors=$((errors+1))
  fi
  if [[ "${SKIP_MCP:-0}" != "1" ]]; then
    for s in context7 sequential-thinking playwright; do
      if mcp_present "$s"; then ok "mcp $s"; else warn "MISSING mcp $s"; errors=$((errors+1)); fi
    done
  fi
  if (( errors > 0 )); then fail "$errors check(s) failed"; fi
  log "All checks passed."
  exit 0
fi

###############################################################################
# 1. Beads CLI
###############################################################################
if command -v bd >/dev/null 2>&1; then
  log "bd already installed: $(bd --version | head -1)"
else
  log "Installing beads CLI via npm…"
  npm install -g @beads/bd
fi

###############################################################################
# 2. Marketplaces
###############################################################################
add_marketplace() {
  local repo="$1"
  if marketplace_present "$repo"; then
    log "Marketplace already added: $repo"
  else
    log "Adding marketplace: $repo"
    claude plugin marketplace add "$repo"
  fi
}
add_marketplace "obra/superpowers"
add_marketplace "steveyegge/beads"
add_marketplace "maslennikov-ig/template-bridge"

###############################################################################
# 3. Plugins
###############################################################################
install_plugin() {
  local pkg="$1"
  local short="${pkg%@*}"
  if plugin_present "$short"; then
    log "Plugin already installed: $short"
  else
    log "Installing plugin: $pkg"
    claude plugin install "$pkg"
  fi
}
install_plugin "superpowers@superpowers-dev"
install_plugin "beads@beads-marketplace"
install_plugin "template-bridge@template-bridge-marketplace"

###############################################################################
# 4. Global ~/.claude/CLAUDE.md — append the workflow block once
###############################################################################
log "Syncing global $CLAUDE_HOME/CLAUDE.md (template-bridge workflow block)…"
git clone --depth 1 "$TB_REPO_URL" "$TB_CACHE_DIR" >/dev/null 2>&1
TB_BLOCK="$TB_CACHE_DIR/CLAUDE.md"
[[ -f "$TB_BLOCK" ]] || fail "template-bridge/CLAUDE.md not found in clone"

GLOBAL_MD="$CLAUDE_HOME/CLAUDE.md"
MARKER="## Workflow: Superpowers + Beads + Templates"
if [[ -f "$GLOBAL_MD" ]] && grep -qF "$MARKER" "$GLOBAL_MD"; then
  log "Global CLAUDE.md already contains the workflow block — skipping."
else
  [[ -s "$GLOBAL_MD" ]] && printf '\n' >> "$GLOBAL_MD"
  cat "$TB_BLOCK" >> "$GLOBAL_MD"
  log "Appended workflow block to $GLOBAL_MD"
fi

###############################################################################
# 5. Hooks — merge into ~/.claude/settings.json without clobbering existing
###############################################################################
log "Merging hooks into $CLAUDE_HOME/settings.json (via jq, additive)…"
SETTINGS="$CLAUDE_HOME/settings.json"
[[ -f "$SETTINGS" ]] || echo '{}' > "$SETTINGS"

REMINDER_CMD='echo '\''WORKFLOW REMINDER: Invoke template-bridge:unified-workflow before any task. Flow: bd create → brainstorm → plan → TDD → review → verify → finish → bd close'\'''

TMP_SETTINGS="$(mktemp)"; TMP_FILES+=("$TMP_SETTINGS")

# add_cmd($event; $cmd): ensure $event has a matcher="" entry with $cmd inside .hooks[];
# skips if any entry under $event already contains $cmd (idempotent).
jq \
  --arg reminder "$REMINDER_CMD" \
  '
  def cmd_present($event; $cmd):
    [(.hooks[$event] // [])[]? | .hooks[]? | .command] | any(. == $cmd);

  def ensure_blank_matcher($event):
    .hooks[$event] = (.hooks[$event] // []) |
    (if (.hooks[$event] | map(.matcher == "") | any) then .
     else .hooks[$event] += [{matcher: "", hooks: []}] end);

  def add_cmd($event; $cmd):
    if cmd_present($event; $cmd) then .
    else
      ensure_blank_matcher($event)
      | .hooks[$event] |= map(
          if .matcher == ""
            then .hooks += [{type: "command", command: $cmd}]
            else . end)
    end;

  .hooks = (.hooks // {})
  | add_cmd("PreCompact";   "bd prime")
  | add_cmd("SessionStart"; "bd prime")
  | add_cmd("SessionStart"; $reminder)
  ' "$SETTINGS" > "$TMP_SETTINGS"

mv "$TMP_SETTINGS" "$SETTINGS"
log "Hooks merged (additive, existing entries preserved)."

###############################################################################
# 6. Template agent: security-auditor
###############################################################################
if [[ -f "$REPO_ROOT/.claude/agents/security-auditor.md" ]]; then
  log "Template agent security-auditor already installed."
else
  log "Installing security-auditor via npx claude-code-templates…"
  (cd "$REPO_ROOT" && npx claude-code-templates@latest --agent security/security-auditor --yes)
fi

###############################################################################
# 7. MCP servers (optional, default ON — set SKIP_MCP=1 to skip)
###############################################################################
if [[ "${SKIP_MCP:-0}" == "1" ]]; then
  log "SKIP_MCP=1 — skipping MCP server registration."
else
  add_mcp() {
    # $1: server name, rest: command + args after `--`
    local name="$1"; shift
    if mcp_present "$name"; then
      log "MCP server already registered: $name"
    else
      log "Registering MCP server: $name"
      claude mcp add --scope user "$name" -- "$@"
    fi
  }
  add_mcp context7            npx -y @upstash/context7-mcp
  add_mcp sequential-thinking npx -y @modelcontextprotocol/server-sequential-thinking
  add_mcp playwright          npx -y @playwright/mcp@latest
fi

###############################################################################
# 8. bd init (project-level) — opt-out via SKIP_BD_INIT=1
###############################################################################
if [[ "${SKIP_BD_INIT:-0}" == "1" ]]; then
  log "SKIP_BD_INIT=1 — skipping bd init."
elif [[ -d "$REPO_ROOT/.beads" ]]; then
  log "beads already initialized in this project."
else
  log "Running bd init in $REPO_ROOT…"
  (cd "$REPO_ROOT" && bd init)
fi

log "Done. Restart Claude Code to pick up plugins, hooks, MCP servers, and CLAUDE.md."
log "Re-run with --verify to confirm: ./setup.sh --verify"
