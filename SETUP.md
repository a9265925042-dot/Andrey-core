# Локальная настройка рабочего пространства

Источник идей: статья на Хабре https://habr.com/ru/articles/1017110/

Стек, который ставится:

- **Superpowers** — TDD / brainstorming / debugging / code review (plugin)
- **Beads** — issue tracker с привязкой к git (plugin + CLI)
- **Template Bridge** — `unified-workflow` + каталог из 413+ агентов (plugin)
- Шаблонный агент `security/security-auditor` через `claude-code-templates`

Все артефакты, которые касаются проекта (`CLAUDE.md`, `AGENTS.md`, `.claude/`, `.beads/`), уже в репозитории.
Глобальные настройки (`~/.claude/CLAUDE.md`, `~/.claude/settings.json`) и плагины
нужно один раз поставить на свою машину — это и делает `setup.sh`.

## Требования

- Node.js 18+ (рекомендуется 20 или 22)
- `claude` CLI — Claude Code (https://docs.claude.com/en/docs/claude-code)
- `git`, `curl`
- macOS / Linux / WSL

## Быстрый старт

```bash
git clone <this-repo>
cd Andrey-core
./setup.sh
```

После завершения скрипта **перезапустите Claude Code**, чтобы он подхватил плагины и hooks.

## Что делает `setup.sh` (по шагам)

1. **Beads CLI**
   ```bash
   npm install -g @beads/bd
   ```

2. **Маркетплейсы плагинов**
   ```bash
   claude plugin marketplace add obra/superpowers
   claude plugin marketplace add steveyegge/beads
   claude plugin marketplace add maslennikov-ig/template-bridge
   ```

3. **Сами плагины**
   ```bash
   claude plugin install superpowers@superpowers-dev
   claude plugin install beads@beads-marketplace
   claude plugin install template-bridge@template-bridge-marketplace
   ```

4. **Глобальный `~/.claude/CLAUDE.md`** — берётся из репозитория template-bridge (MIT),
   там лежит короткий блок «9-шаговый workflow + правила». Скрипт дописывает его,
   если ещё не добавлен (идемпотентно).

5. **Hooks в `~/.claude/settings.json`** — добавляются `SessionStart` и `PreCompact`:
   - `bd prime` — подгружает контекст beads в начале сессии и перед компакцией
   - `echo 'WORKFLOW REMINDER: ...'` — напоминание про `unified-workflow`

   Существующие hooks (например `Stop`) сохраняются — merge выполняется через `jq`.

6. **Шаблонный агент**
   ```bash
   npx claude-code-templates@latest --agent security/security-auditor --yes
   ```
   Кладёт `security-auditor.md` в `.claude/agents/`.

7. **`bd init`** — инициализирует beads в проекте, если ещё не сделано.
   Создаёт `.beads/`, `AGENTS.md`, проектный `CLAUDE.md` (с интеграцией beads)
   и проектный `.claude/settings.json`.

## Проверка

```bash
claude plugin list                  # должны быть три плагина, все enabled
bd --version                        # 1.x
ls ~/.claude/CLAUDE.md              # есть блок «Workflow: Superpowers + Beads + Templates»
jq '.hooks | keys' ~/.claude/settings.json   # содержит SessionStart, PreCompact, Stop
ls .claude/agents/                  # security-auditor.md присутствует
```

## Дальше — как работать

Перед любой задачей вызвать в чате Claude Code:

```
template-bridge:unified-workflow
```

Дальше идёт стандартный цикл из 9 шагов:
`bd create → brainstorm → plan → TDD (red-green-refactor) → review → verify → finish → bd close`.

## Источники и лицензии

- Superpowers — https://github.com/obra/superpowers (Jesse Vincent)
- Beads — https://github.com/steveyegge/beads (Steve Yegge)
- Template Bridge — https://github.com/maslennikov-ig/template-bridge (MIT)
- claude-code-templates — https://github.com/davila7/claude-code-templates (Daniel Avila)

Содержимое `~/.claude/CLAUDE.md` (workflow + правила) скопировано из
MIT-лицензированного [`template-bridge/CLAUDE.md`](https://github.com/maslennikov-ig/template-bridge/blob/main/CLAUDE.md) — это явно
рекомендуемый способ установки в README того проекта.
