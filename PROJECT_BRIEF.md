# Agent Engine — Project Brief

## What we're building

A FastAPI service that runs AI agents as a virtual dev team. The team takes a task (e.g. "fix auth across services") with references to 1..N git repositories, analyzes all the code, plans changes, writes code, runs tests locally in sandbox, creates PRs, and handles CI failures automatically.

This is **Milestone 1** — the working dev pipeline. Future milestones add more agent categories (security, devops, docs) and meta-agents that create new agents.

---

## Tech stack

- **Python 3.12** with async everywhere
- **FastAPI** + uvicorn (single process, no Celery, no Redis)
- **LangGraph** for pipeline orchestration
- **SQLAlchemy 2.x async** + asyncpg
- **PostgreSQL 16** with pgvector extension
- **Docker SDK** for sandbox execution
- **OpenAI SDK** (async) for LLM calls
- **Alembic** for migrations
- **pytest** + pytest-asyncio for tests
- Dependencies via `requirements/` (base.txt, dev.txt, prod.txt)

---

## Architecture principles

**Layered with strict dependency direction:**

```
api → services → engine → agents → tools / memory → db
```

Each layer imports only from the layer below. No reverse imports.

**All I/O is async.** LLM calls, GitHub API, DB queries, Docker — everything uses `await`. One uvicorn process handles dozens of concurrent pipelines because 95% of time is spent waiting on external APIs.

**No background workers on M1.** Pipelines run inside FastAPI via async generators. WebSocket streams events directly from the executor. Long-running CI runs on GitHub's side, not ours — we react via webhooks.

**Plugin architecture for agents.** Each agent is a folder with `agent.py`, `prompts.py`, and `__init__.py` that auto-registers in the `AgentRegistry`. Adding a new agent doesn't modify existing code.

**Protocol-based integrations.** `GitProvider`, `SandboxRunner`, `LLMGateway` are Python protocols. Swapping GitHub for GitLab, or Docker for E2B, means adding one file in `providers/`.

---

## Folder structure

```
agent-engine/
├── requirements/
│   ├── base.txt              # core deps
│   ├── dev.txt               # -r base.txt + test/lint
│   └── prod.txt              # -r base.txt + monitoring
├── Dockerfile
├── docker-compose.yaml       # postgres:pgvector + app only
├── Makefile                  # dev, test, migrate, lint
├── alembic.ini
├── setup.cfg
├── .env.example
│
└── src/
    ├── config/
    │   ├── settings.py       # pydantic BaseSettings from env
    │   └── constants.py      # MAX_FIX_ATTEMPTS=3, timeouts, defaults
    │
    ├── api/
    │   ├── app.py            # create_app(), lifespan, CORS
    │   ├── deps.py           # Depends() for db, auth, services
    │   ├── errors.py         # exception → HTTP response
    │   ├── middleware.py     # request_id, timing
    │   ├── routes/
    │   │   ├── tasks.py      # POST /tasks, GET /tasks/{id}
    │   │   ├── projects.py   # CRUD projects + attach repos
    │   │   ├── agents.py     # CRUD custom agents (M2 stub)
    │   │   ├── pipelines.py  # CRUD pipelines (M2 stub)
    │   │   ├── tools.py      # CRUD MCP connections (M2 stub)
    │   │   ├── webhooks.py   # POST /webhooks/github
    │   │   └── health.py
    │   ├── ws/
    │   │   └── task_stream.py  # WS /ws/tasks/{id}
    │   └── schemas/
    │       ├── task_schemas.py
    │       ├── project_schemas.py
    │       ├── agent_schemas.py
    │       ├── pipeline_schemas.py
    │       └── webhook_schemas.py
    │
    ├── services/             # all business logic
    │   ├── task_service.py       # create, run, handle CI result, retry
    │   ├── project_service.py    # create, attach repos, validate
    │   ├── pipeline_service.py   # build graph, execute, stream
    │   ├── memory_service.py     # recall, save, reindex
    │   ├── auth_service.py       # decode DRF JWT (shared secret)
    │   └── webhook_service.py    # verify signature, parse CI status
    │
    ├── engine/               # pipeline orchestration
    │   ├── registry.py       # AgentRegistry singleton
    │   ├── graph_builder.py  # config → LangGraph StateGraph
    │   ├── executor.py       # astream, yield events, checkpoint
    │   ├── state.py          # TaskState TypedDict
    │   └── routers/
    │       ├── review_router.py   # approve / reject
    │       └── test_router.py     # pass / fail
    │
    ├── agents/               # virtual dev team
    │   ├── base.py           # BaseAgent ABC: async execute(state)→dict
    │   └── development/
    │       ├── tech_lead/          # scan repos, build context
    │       ├── architect/          # design solution, create plan
    │       ├── senior_developer/   # write code, handle review feedback
    │       ├── code_reviewer/      # approve / request changes
    │       ├── qa_engineer/        # run sandbox, analyze results
    │       ├── release_manager/    # create PRs, save memory
    │       └── devops_engineer/    # handle CI failures (webhook-triggered)
    │
    ├── tools/                # external integrations
    │   ├── git/
    │   │   ├── provider.py       # GitProvider Protocol
    │   │   ├── factory.py        # url → provider instance
    │   │   └── providers/
    │   │       └── github.py     # M1: GitHub API + MCP
    │   ├── sandbox/
    │   │   ├── runner.py         # SandboxRunner Protocol
    │   │   ├── result.py         # SandboxResult dataclass
    │   │   └── runners/
    │   │       └── docker_runner.py
    │   ├── llm/
    │   │   ├── gateway.py        # LLMGateway Protocol
    │   │   ├── router.py         # agent role → model
    │   │   └── providers/
    │   │       └── openai.py
    │   └── mcp/
    │       ├── manager.py        # connection lifecycle
    │       ├── client.py         # generic JSON-RPC wrapper
    │       └── servers/
    │           └── github_mcp.py
    │
    ├── memory/               # all in PostgreSQL + pgvector
    │   ├── manager.py        # unified get_context / save
    │   ├── episodic.py       # past tasks, vector search
    │   ├── semantic.py       # code chunks index
    │   ├── embeddings.py     # async generate + LRU cache
    │   └── chunkers.py       # AST / tree-sitter split
    │
    ├── db/
    │   ├── session.py        # async engine, sessionmaker
    │   ├── base.py           # DeclarativeBase, UserScopeMixin
    │   ├── models/
    │   │   ├── project.py        # Project, ProjectRepo
    │   │   ├── task.py           # Task (status, attempt, state JSONB)
    │   │   ├── agent.py          # AgentRecord (M2 ready)
    │   │   ├── tool.py           # ToolRecord (M2 ready)
    │   │   ├── pipeline.py       # PipelineRecord (M2 ready)
    │   │   ├── memory.py         # Episode, CodeChunk with Vector(1536)
    │   │   └── checkpoint.py     # LangGraph checkpoints
    │   ├── queries/
    │   │   ├── task_queries.py
    │   │   ├── project_queries.py
    │   │   └── memory_queries.py  # vector similarity + filters
    │   └── migrations/
    │       ├── env.py
    │       └── versions/         # 001_init includes CREATE EXTENSION vector
    │
    └── common/
        ├── exceptions.py     # AppError hierarchy
        ├── retry.py          # async retry with exp backoff
        └── crypto.py         # Fernet for MCP credentials
```

---

## Data model

**Project** — a user creates a project and attaches 1..N repositories from any git provider (start with GitHub).

**Task** — belongs to a project, has description, status (`running`, `awaiting_ci`, `fixing`, `completed`, `needs_human`), attempt counter, state JSONB, pr_urls JSONB.

**Memory** — `episodes` table stores past tasks with embeddings. `code_chunks` stores indexed code by function/class.

All tables have `user_id` FK. PostgreSQL Row Level Security ensures isolation per user.

---

## Pipeline flow (milestone 1)

```
Task input (description + repo refs)
       ↓
Tech Lead            — scans all repos, finds relevant code, builds context
       ↓
Architect            — creates cross-repo change plan
       ↓
Senior Developer     — writes code, creates branches per repo
       ↓
Code Reviewer        — APPROVE or REQUEST_CHANGES (→ back to Senior Dev, max 3)
       ↓
QA Engineer          — runs sandbox per repo, PASS or FAIL (→ back to Senior Dev, max 3)
       ↓
Release Manager      — creates PRs, saves memory
       ↓
Task = awaiting_ci
       ↓
[GitHub runs CI, sends webhook]
       ↓
If fail:
  DevOps Engineer    — fetch logs, analyze, fix, push to same branch
  Task = awaiting_ci (attempt++), max 3
If pass:
  Task = completed
If 3 fails:
  Task = needs_human
```

All this runs async inside one uvicorn process. WebSocket streams events to frontend directly from executor's async generator.

---

## Integration with existing system

- **React frontend** talks to this service at `/api/v1/*` and `/ws/*`
- **Django DRF** owns users/billing, issues JWT tokens
- **FastAPI** validates the JWT with shared secret (no HTTP call to DRF)
- Both services share one PostgreSQL instance with separate table ownership
- Nginx/Traefik routes by path prefix in production

---

## Agent roles (development team)

| Agent | Role | Responsibility |
|-------|------|----------------|
| `tech_lead` | Tech Lead | Scans all repos, understands architecture, builds unified context |
| `architect` | Architect | Reads context, designs solution, creates detailed change plan |
| `senior_developer` | Senior Developer | Writes code, creates branches, handles review feedback |
| `code_reviewer` | Code Reviewer | APPROVE or REQUEST_CHANGES with specific feedback |
| `qa_engineer` | QA Engineer | Runs sandbox, analyzes test output, decides PASS/FAIL |
| `release_manager` | Release Manager | Creates PRs, writes descriptions, saves to memory |
| `devops_engineer` | DevOps Engineer | Handles CI failures via webhook, fetches logs, generates fix |

---

## Implementation order

Build in this order so each step has something to test against:

1. **Skeleton** — folder structure, `requirements/base.txt`, Dockerfile, docker-compose with postgres+pgvector, Alembic setup, `config/settings.py`
2. **DB layer** — SQLAlchemy async session, base models (User ref, Project, ProjectRepo, Task), first migration with pgvector extension
3. **Auth** — `auth_service.py` JWT decode, `deps.py` `get_current_user`
4. **Minimal API** — `routes/projects.py` and `routes/tasks.py` (create + get), schemas
5. **Engine core** — `BaseAgent`, `AgentRegistry`, `state.py`, minimal `graph_builder.py` and `executor.py`
6. **Tools foundation** — `GitProvider` protocol + `github.py` provider, `SandboxRunner` + `docker_runner.py`, `LLMGateway` + `openai.py`
7. **First agent** — `tech_lead` (simplest — just reads repos, builds context)
8. **Remaining dev team** — architect, senior_developer, code_reviewer, qa_engineer, release_manager
9. **WebSocket streaming** — `ws/task_stream.py` consuming executor's async generator
10. **Memory layer** — `manager.py`, `episodic.py`, `semantic.py`, pgvector queries
11. **Webhook flow** — `routes/webhooks.py` + `devops_engineer` agent + retry logic in `task_service`
12. **Tests** — unit tests for each agent with mocked LLM, integration test for full pipeline
13. **M2 stubs** — empty `routes/agents.py`, `routes/pipelines.py`, `routes/tools.py` with schemas ready but returning 501

---

## Key rules

- Never block the event loop — if something is CPU-heavy (like AST parsing large files), use `asyncio.to_thread`
- Every agent's `execute()` is async and returns only state diffs
- Every LLM call uses `LLMGateway.invoke()` — agents never import `openai` directly
- Every git operation goes through `GitProvider` — agents never import `httpx` for GitHub
- Prompts always live in `prompts.py` next to the agent, never inline in `agent.py`
- Every DB table has `user_id` and is filtered by it in every query
- Connection lifecycle for MCP: connect on task start, disconnect on task end, one pool per user
- Sandbox containers have strict timeout (`SANDBOX_TIMEOUT_SEC`) and resource limits (RAM, CPU)
- All external API calls (LLM, GitHub, Docker) are wrapped in `retry.py` with exponential backoff
