# Локальная настройка рабочего пространства

Источник идей: статья на Хабре https://habr.com/ru/articles/1017110/

Стек, который ставится:

- **Superpowers** — TDD / brainstorming / debugging / code review (plugin)
- **Beads** — issue tracker с привязкой к git (plugin + CLI `bd`)
- **Template Bridge** — `unified-workflow` + каталог из 413+ агентов (plugin)
- Шаблонный агент `security/security-auditor` через `claude-code-templates`
- MCP-серверы: **Context7**, **Sequential Thinking**, **Playwright** (опционально)

Артефакты проекта (`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.beads/`) уже в репозитории.
Глобальные настройки (`~/.claude/CLAUDE.md`, `~/.claude/settings.json`, MCP-серверы) и плагины
ставятся один раз — этим занимается `setup.sh`.

## Требования

- Node.js 18+ (рекомендуется 20 или 22)
- `claude` CLI — Claude Code (https://docs.claude.com/en/docs/claude-code)
- `git`, `jq` (для безопасного merge в `settings.json`)
- macOS / Linux / WSL

## Быстрый старт

```bash
git clone <this-repo>
cd Andrey-core
./setup.sh                # установить всё
./setup.sh --verify       # только проверить состояние, без изменений
```

После установки **перезапустите Claude Code**, чтобы он подхватил плагины, hooks и MCP-серверы.

## Флаги и переменные окружения

| Флаг / env             | Действие                                              |
| ---------------------- | ----------------------------------------------------- |
| `--verify`             | Запустить только health-checks, ничего не менять      |
| `SKIP_MCP=1`           | Не регистрировать MCP-серверы                         |
| `SKIP_BD_INIT=1`       | Не запускать `bd init` в репозитории                  |
| `CLAUDE_HOME=...`      | Переопределить `~/.claude`                            |

## Что делает `setup.sh` (по шагам)

1. **Beads CLI** — `npm install -g @beads/bd`, если ещё не стоит.
2. **Маркетплейсы плагинов:**
   ```bash
   claude plugin marketplace add obra/superpowers
   claude plugin marketplace add steveyegge/beads
   claude plugin marketplace add maslennikov-ig/template-bridge
   ```
3. **Плагины:**
   ```bash
   claude plugin install superpowers@superpowers-dev
   claude plugin install beads@beads-marketplace
   claude plugin install template-bridge@template-bridge-marketplace
   ```
4. **`~/.claude/CLAUDE.md`** — дописывает workflow-блок из MIT-лицензированного
   `template-bridge/CLAUDE.md` (короткий, 9 шагов + правила). Идемпотентно: маркер-
   заголовок не дублируется.
5. **Hooks в `~/.claude/settings.json`** — через `jq` **аддитивно** добавляет:
   - `PreCompact`: `bd prime`
   - `SessionStart`: `bd prime` + reminder про `unified-workflow`

   Существующие записи (например, `Stop`-hook) сохраняются. Повторный запуск не
   дублирует команды.
6. **Шаблонный агент:** `npx claude-code-templates@latest --agent security/security-auditor --yes`
   кладёт `security-auditor.md` в `.claude/agents/`.
7. **MCP-серверы** (можно пропустить через `SKIP_MCP=1`):
   ```bash
   claude mcp add --scope user context7            -- npx -y @upstash/context7-mcp
   claude mcp add --scope user sequential-thinking -- npx -y @modelcontextprotocol/server-sequential-thinking
   claude mcp add --scope user playwright          -- npx -y @playwright/mcp@latest
   ```
8. **`bd init`** в репозитории (если ещё не сделано) — создаёт `.beads/`,
   `AGENTS.md`, проектный `CLAUDE.md` и `.claude/settings.json`.

## Проверка

```bash
./setup.sh --verify
```

Выводит зелёные `[ ok ]` для каждой проверки или красные `[warn]` для пропусков.
Завершается ненулевым кодом, если что-то отсутствует — удобно для CI / pre-flight.

## Дальше — как работать

В чате Claude Code перед любой задачей:

```
template-bridge:unified-workflow
```

Стандартный цикл из 9 шагов:
`bd create → brainstorm → plan → TDD (red-green-refactor) → review → verify → finish → bd close`.

## Источники и лицензии

- Superpowers — https://github.com/obra/superpowers (Jesse Vincent)
- Beads — https://github.com/steveyegge/beads (Steve Yegge)
- Template Bridge — https://github.com/maslennikov-ig/template-bridge (MIT)
- claude-code-templates — https://github.com/davila7/claude-code-templates (Daniel Avila)

Содержимое глобального `~/.claude/CLAUDE.md` (workflow + правила) скопировано из
MIT-лицензированного [`template-bridge/CLAUDE.md`](https://github.com/maslennikov-ig/template-bridge/blob/main/CLAUDE.md) — это
рекомендуемый способ установки согласно README того проекта.
