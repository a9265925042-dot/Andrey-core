#!/usr/bin/env bash
# Local workspace bootstrap — see SETUP.md for the full breakdown.
# Idempotent: safe to run multiple times.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
TB_REPO_URL="https://github.com/maslennikov-ig/template-bridge.git"
TB_CACHE_DIR="${TMPDIR:-/tmp}/template-bridge-setup"

log()  { printf '\033[1;34m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m[fail]\033[0m %s\n' "$*" >&2; exit 1; }

require() { command -v "$1" >/dev/null 2>&1 || fail "Missing required command: $1"; }

require node
require npm
require npx
require git
require claude

# jq is needed to merge ~/.claude/settings.json without clobbering existing hooks.
if ! command -v jq >/dev/null 2>&1; then
  warn "jq not found. Settings.json hooks merge requires jq."
  warn "Install: brew install jq  |  apt-get install jq  |  https://stedolan.github.io/jq/"
  fail "Please install jq and re-run."
fi

mkdir -p "$CLAUDE_HOME"

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
  if claude plugin marketplace list 2>/dev/null | grep -q "$repo"; then
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
  local pkg="$1"   # e.g. superpowers@superpowers-dev
  local short="${pkg%@*}"
  if claude plugin list 2>/dev/null | grep -q "^\s*>\s*${short}@"; then
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
# 4. Global ~/.claude/CLAUDE.md (append the template-bridge workflow block)
###############################################################################
log "Syncing global ~/.claude/CLAUDE.md (template-bridge workflow block)…"
rm -rf "$TB_CACHE_DIR"
git clone --depth 1 "$TB_REPO_URL" "$TB_CACHE_DIR" >/dev/null 2>&1
TB_BLOCK="$TB_CACHE_DIR/CLAUDE.md"
[ -f "$TB_BLOCK" ] || fail "template-bridge/CLAUDE.md not found in clone"

GLOBAL_MD="$CLAUDE_HOME/CLAUDE.md"
MARKER="## Workflow: Superpowers + Beads + Templates"
if [ -f "$GLOBAL_MD" ] && grep -qF "$MARKER" "$GLOBAL_MD"; then
  log "Global CLAUDE.md already contains the workflow block — skipping."
else
  if [ -s "$GLOBAL_MD" ]; then
    printf '\n' >> "$GLOBAL_MD"
  fi
  cat "$TB_BLOCK" >> "$GLOBAL_MD"
  log "Appended workflow block to $GLOBAL_MD"
fi

###############################################################################
# 5. Merge hooks into ~/.claude/settings.json (preserve existing keys)
###############################################################################
log "Merging hooks into $CLAUDE_HOME/settings.json (via jq)…"

SETTINGS="$CLAUDE_HOME/settings.json"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"

# JSON describing only the SessionStart + PreCompact hooks we want to add.
read -r -d '' HOOK_PATCH <<'JSON' || true
{
  "hooks": {
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "bd prime" }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "",
        "hooks": [
          { "type": "command", "command": "bd prime" },
          { "type": "command", "command": "echo 'WORKFLOW REMINDER: Invoke template-bridge:unified-workflow before any task. Flow: bd create → brainstorm → plan → TDD → review → verify → finish → bd close'" }
        ]
      }
    ]
  }
}
JSON

TMP_SETTINGS="$(mktemp)"
jq --argjson patch "$HOOK_PATCH" '
  .hooks = (.hooks // {}) |
  .hooks.PreCompact   = ($patch.hooks.PreCompact)   |
  .hooks.SessionStart = ($patch.hooks.SessionStart)
' "$SETTINGS" > "$TMP_SETTINGS"
mv "$TMP_SETTINGS" "$SETTINGS"
log "Hooks merged. Existing keys preserved."

###############################################################################
# 6. Template agent: security-auditor
###############################################################################
if [ -f "$REPO_ROOT/.claude/agents/security-auditor.md" ]; then
  log "Template agent security-auditor already installed."
else
  log "Installing security-auditor via npx claude-code-templates…"
  (cd "$REPO_ROOT" && npx claude-code-templates@latest --agent security/security-auditor --yes)
fi

###############################################################################
# 7. bd init (project-level)
###############################################################################
if [ -d "$REPO_ROOT/.beads" ]; then
  log "beads already initialized in this project."
else
  log "Running bd init in $REPO_ROOT…"
  (cd "$REPO_ROOT" && bd init)
fi

###############################################################################
# Done
###############################################################################
log "Done. Restart Claude Code to pick up plugins, hooks, and CLAUDE.md."
log "Verify with: claude plugin list  &&  bd --version  &&  jq '.hooks|keys' \"$SETTINGS\""
