# Clyde AI — Agent Engine

> SaaS-платформа з командою AI-агентів, які планують, пишуть і доставляють код одночасно у багатьох репозиторіях.

Clyde AI — це бекенд віртуальної команди розробників. Користувач підключає свої акаунти (GitHub, Jira, Slack, Google, AWS, Azure), описує задачу — а агенти самі читають код, пишуть зміни, відкривають PR, оновлюють тікети та звітують у Slack. Стан кожної задачі зберігається в Postgres з довготривалою пам'яттю на pgvector, тому агенти "пам'ятають" контекст між сесіями.

## Що це

Це **API-сервіс на FastAPI**, який оркеструє роботу AI-агентів поверх **Claude Agent SDK** від Anthropic. Окрема SPA/фронтенд у цьому репо немає — він спілкується з бекендом через REST + WebSocket.

### Ключові можливості

- **Команди агентів (orchestrator + subagents).** Один головний оркестратор делегує підзадачі спеціалізованим subagent-ам (наприклад, `cloud-fixer`, `publisher`). Користувач сам збирає свою команду та налаштовує, які інструменти кожен з них бачить.
- **MCP-інтеграції з нуля.** GitHub, Jira (через `mcp-atlassian`), Slack, Google, AWS, Azure — підключаються через OAuth і доступні агентам як інструменти. Додавання нового провайдера задокументовано в `src/integrations/README.md` і займає ~20 рядків коду.
- **Довготривала пам'ять** на Postgres + pgvector + Voyage AI embeddings. Граф пам'яті зберігає факти про користувача, репозиторії й попередні задачі.
- **LangGraph-пайплайн** з контрольними точками в Postgres — задачу можна зупинити, відновити, перезапустити з місця збою.
- **Approval flow.** Перш ніж зробити щось деструктивне (push, merge, видалення), агент чекає підтвердження користувача через WebSocket.
- **Sandbox для виконання коду** у Docker-контейнерах (через `docker.sock`).
- **Версіоновані конфіги агентів.** Кожне збереження — це нова версія, відкат = перемикання вказівника.
- **Multi-environment деплой:** локальний docker-compose, dev (AWS RDS), prod.

### Стек

Python 3.12 · FastAPI · SQLAlchemy 2 (async) · Alembic · Postgres 16 + pgvector · Redis · LangGraph · Anthropic Claude Agent SDK · Voyage AI · Authlib · PyJWT · structlog · Docker.

## Структура репозиторію

```
src/
├── api/              # FastAPI app, роутери, схеми, websockets, middleware
├── agents/           # Базовий SDKAgent, шаблони (orchestrator, publisher), промпти
├── agent_tools/      # Кастомні інструменти (@tool SDK servers) + MCP gate
├── integrations/     # OAuth-провайдери: github, jira, slack, google, aws, azure
├── credentials/      # Зберігання та розшифрування OAuth-токенів (Fernet)
├── services/         # Бізнес-логіка: tasks, projects, auth, webhooks, chat
├── db/               # Моделі, міграції Alembic, репозиторії, password provider
├── config/           # Settings (pydantic-settings), constants
└── utils/

scripts/              # E2E-тести й сценарії (approval flow, memory graph, Slack MCP, ...)
tests/                # unit + integration
requirements/         # base.txt, dev.txt, prod.txt
```

## Запуск

### Передумови

- Docker + Docker Compose (увесь стек крутиться в контейнерах — локального venv не треба)
- `make` (опційно — у репозиторії є зручний Makefile)
- API-ключ Anthropic (`ANTHROPIC_API_KEY`)
- OAuth-додатки для тих провайдерів, які хочеш використати (мінімум — GitHub)

### 1. Клонування і конфіг

```bash
git clone https://github.com/Tk4V/SAAS-AI-AGENTS-LONG-TERM.git
cd SAAS-AI-AGENTS-LONG-TERM
cp .env.example .env
```

Відкрий `.env` і заповни як мінімум:

- `ANTHROPIC_API_KEY` — ключ від console.anthropic.com
- `JWT_SECRET` — будь-який довгий рядок (має збігатися з твоїм Django/DRF, якщо інтегруєш)
- `FERNET_KEY` — згенерувати: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
- OAuth-credentials для потрібних провайдерів (`GITHUB_OAUTH_CLIENT_ID/SECRET` тощо)

> **Redirect URI для OAuth-додатків:** `http://localhost:8000/api/v1/credentials/oauth/<provider>/callback`

### 2. Підняти локальний стек

```bash
make up
```

Команда збере образ, підніме Postgres (з pgvector), Redis, прокотить Alembic-міграції й запустить FastAPI з hot-reload на `http://localhost:8000`. Логи додатку покажуться в терміналі.

Без `make`:

```bash
docker compose -f docker-compose-local.yaml up --build
```

### 3. Перевірити

- Health: `http://localhost:8000/api/v1/health`
- OpenAPI docs: `http://localhost:8000/docs`
- Postgres: `localhost:5432` (юзер/пароль/база з `.env`, за замовчанням `clyde/clyde/clyde`)

## Часті команди (Makefile)

| Команда | Що робить |
|---|---|
| `make up` | Підняти весь локальний стек |
| `make down` | Зупинити |
| `make restart` | Перезапустити лише app |
| `make rebuild` | Перебудувати образи й перезапустити |
| `make logs` | Тейл логів усіх сервісів |
| `make shell` | Bash всередині app-контейнера |
| `make psql` | psql всередині postgres-контейнера |
| `make migrate` | Прокотити нові Alembic-міграції |
| `make makemigration MSG="add foo"` | Згенерувати нову ревізію |
| `make reset-db` | Знести volumes і прокотити міграції заново |
| `make lint` / `make format` | ruff |
| `make typecheck` | mypy (strict) |
| `make test` / `make test-cov` | pytest (+ coverage) |
| `make pre-commit` | Прогнати всі pre-commit хуки |

Інші середовища: `make dev` (AWS RDS dev) і `make prod` — вони читають той самий `.env`, але з відповідних compose-файлів.

## Робочий процес (high-level)

1. Користувач логіниться (JWT, виданий Django/DRF-сервісом, перевіряється тут).
2. Через `/api/v1/credentials/oauth/<provider>/authorize` підключає GitHub, Jira тощо. Токени шифруються Fernet і лягають у `credentials`.
3. Створює проект (`POST /projects`), додає репозиторії.
4. Створює агента (`POST /agents`), вибираючи з каталогу subagent-ів і MCP-інтеграцій.
5. Кидає задачу (`POST /tasks`) — пайплайн запускається, прогрес стрімиться через WebSocket (`/ws/tasks/{id}`).
6. На деструктивних кроках агент чекає approval (`POST /tasks/{id}/approvals/{step}`).

E2E-сценарії живуть у `scripts/` — `e2e_test.py`, `test_approval_flow.py`, `test_memory_graph.py`, `test_finance_app_task.py` і подібні.

## Розширення

- **Новий OAuth-провайдер:** покроково в `src/integrations/README.md` (~20 рядків коду).
- **Новий кастомний інструмент:** реєструється в `src/agent_tools/` як `@tool` SDK server + запис у custom registry. З'явиться в `GET /tools` автоматично.
- **Новий шаблон агента:** новий підклас `SDKAgent` + значення enum для `agent_configs.template_name`.
- **Деталі про catalog/registry/tools:** `src/agents/README.md`.

## Якість коду

Lint (`ruff`), формат (`ruff format`), типи (`mypy --strict`), тести (`pytest` з маркерами `integration`/`slow`), pre-commit хуки. Все запускається всередині контейнера через `make ...`.

## Ліцензія

У репозиторії наразі не вказана — додай `LICENSE` перед публічним релізом.
