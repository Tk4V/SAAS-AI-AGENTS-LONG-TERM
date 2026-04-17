# Clyde AI — Milestone 1 Progress Report

Status: **Steps 0-11 completed**, Step 12 (M2 stubs) pending, Step 13 (production polish) pending.

## Codebase snapshot

| Metric | Count |
|---|---|
| Source files (non-empty `.py` in `src/`) | 109 |
| Test files (non-empty `.py` in `tests/`) | 15 |
| Total Python lines (src + tests) | ~10 500 |
| ORM models | 9 tables |
| Alembic migrations | 2 |
| API endpoints | 16 |
| WebSocket endpoints | 1 |
| Agent classes | 7 |

### Lines per module

| Module | Files | Lines |
|---|---|---|
| `src/config` | 3 | 177 |
| `src/common` | 4 | 289 |
| `src/db` | 19 | 1 377 |
| `src/api` | 18 | 1 320 |
| `src/engine` | 9 | 702 |
| `src/agents` | 27 | 2 381 |
| `src/services` | 6 | 893 |
| `src/tools` | 17 | 1 103 |
| `src/memory` | 6 | 677 |
| `tests/` | 15 | 1 555 |

## Completed steps

### Step 0 — Skeleton and tooling

- Project structure aligned to Brief: `routes/`, `schemas/`, `websockets/` moved inside `src/api/`
- Renamed `Development_team/` to `development_team/` (snake_case)
- Filled: `requirements/{base,dev,prod}.txt` with 2026-era pinned versions
- Multi-stage `Dockerfile` (Python 3.12-slim, non-root user, healthcheck)
- Three docker-compose files: `local` (Postgres only), `dev` (staging, no local DB), `prod` (RDS)
- `Makefile` with install/dev/test/migrate/lint/format targets
- `pyproject.toml` with ruff + mypy + pytest + coverage config
- `.pre-commit-config.yaml` with ruff + mypy hooks
- `alembic.ini` + `migrations/env.py` (async asyncpg engine)
- `.env.example`, `.gitignore`, `.dockerignore`

### Step 1 — Database layer

- `Settings` class (`pydantic-settings`): all env vars, `database_url` built via `URL.create()` from `DB_*` parts
- `constants.py`: pipeline status strings, WS event names, chunk/recall limits
- `AppError` hierarchy: `NotFound`, `AlreadyExists`, `Validation`, `Authentication`, `Authorization`, `Conflict`, `ExternalService`, `Pipeline`, `Sandbox`
- `Base` (DeclarativeBase) with `UUIDPrimaryKeyMixin`, `TimestampMixin`, `UserScopeMixin`
- `Database` class: async engine + sessionmaker + `get_session()` dep + `session_scope()` ctx manager
- 9 ORM models: `Project`, `ProjectRepo`, `Task`, `Episode`, `CodeChunk`, `UserOAuthCredential`, `AgentRecord` (M2), `ToolRecord` (M2), `PipelineRecord` (M2)
- Migration #1: `CREATE EXTENSION vector`, 8 tables, HNSW indexes on vector columns
- Migration #2: `user_oauth_credentials` table

### Step 2 — Authentication

- `AuthService` class: JWT HS256 decode with leeway, audience opt-in, error mapping
- `CurrentUser` frozen dataclass (id, username, email, raw_claims)
- `get_current_user` dep (HTTP Bearer), `get_current_user_ws` dep (WS `?token=` query param)
- `ExceptionHandlerRegistry` class: maps `AppError` to JSON responses
- `RequestContextMiddleware`: request_id, timing, structlog context binding
- `Application` class: `build()` factory, lifespan, CORS, middleware, handlers, routers
- `routes/health.py`: `/health` (liveness) and `/ready` (DB check)

### Step 3 — Minimal API

- Pydantic schemas: `ProjectCreate/Read/Update/ListItem`, `ProjectRepoCreate/Read`, `TaskCreate/Read/ListItem`, `Page[T]`, `PaginationParams`, `ErrorResponse`
- All schemas have `from_orm()` classmethods for ORM-to-DTO mapping
- `ProjectRepository`, `TaskRepository` classes with full CRUD + user_id filtering
- `ProjectService`, `TaskService` thin wrappers over repositories
- REST endpoints:
  - `POST/GET/PATCH/DELETE /projects`, `POST/DELETE /projects/{id}/repos`
  - `POST/GET /tasks`, `GET /tasks/{id}` (filter by project_id + status)
- DI chain: `SessionDep -> RepositoryDep -> ServiceDep -> route handler`

### Step 4 — Engine core

- `BaseAgent` ABC: `name`/`role` ClassVars, `__call__` wrapper with logging, abstract `execute(state)`
- `AgentRegistry` singleton: `register()` decorator, `get()`, `all()`, `autoload(package)` walks subpackages
- `TaskState` TypedDict (total=False): identifiers, repos, context, plan, diffs, review/QA state, pr_urls, attempt, error, events (with `operator.add` reducer)
- Helper TypedDicts: `RepoSnapshot`, `CodeChange`, `SandboxOutcome`, `PipelineEvent`
- `ReviewRouter`: approve to QA, reject to Senior Dev, exhausted to END
- `TestRouter`: pass to Release Manager, fail to Senior Dev, exhausted to END
- `CheckpointerManager`: psycopg pool + `AsyncPostgresSaver.setup()`
- `PipelineExecutor`: `stream()`, `resume()`, `get_state()` around compiled LangGraph graph
- `PipelineGraphBuilder`: `build_default()` compiles the M1 dev pipeline
- `EngineRuntime`: unified lifecycle (setup: checkpointer + autoload; dispose; lazy executor)
- Settings: added `database_url_libpq` for psycopg pool

### Step 5 — Tools foundation

- `RetryPolicy` class (tenacity wrapper, exp backoff with jitter)
- `RetryPresets`: `for_llm()` (4 attempts), `for_github()` (3), `for_sandbox()` (2)
- `TokenCipher` class (Fernet encrypt/decrypt for OAuth tokens)
- `LLMGateway` ABC: `chat()` + `stream()` methods
- `ChatMessage`, `ChatResponse`, `TokenUsage` value objects
- `ModelRouter`: role to model mapping (opus/sonnet/haiku)
- `AnthropicLLMGateway`: retry-wrapped `messages.create` + `messages.stream`
- `GitProvider` ABC: full contract (parse URL, OAuth flow, clone, push, PR, CI logs, revoke)
- `GitHubProvider`: OAuth URLs, code exchange, shallow clone (gitpython + to_thread), PR creation (httpx), workflow log fetch, token revocation (DELETE /applications/{client_id}/token)
- `GitProviderFactory`: URL to provider, caching, `aclose()`
- `SandboxRunner` ABC + `SandboxResult` dataclass
- `DockerSandboxRunner`: fresh container per call, network=none, mem/cpu limits, timeout handling, auto-pull images
- `Toolbox` singleton: lazy properties for llm, git, sandbox, cipher, embedder
- DI in deps.py: `LLMGatewayDep`, `GitFactoryDep`, `SandboxRunnerDep`, `TokenCipherDep`

### Step 5.5 — GitHub OAuth on FastAPI side

- `UserOAuthCredential` model + migration (UNIQUE per user/provider)
- `UserOAuthCredentialRepository`: upsert, get, list, delete
- `OAuthStateSigner`: JWT-signed state token (10 min TTL, type-verified)
- `OAuthService`: `start_flow()`, `handle_callback()`, `get_token()`, `list_for_user()`, `revoke()` (real GitHub API revoke)
- OAuth endpoints:
  - `GET /auth/oauth/{provider}/start` (requires JWT)
  - `GET /auth/oauth/{provider}/callback` (no JWT, uses signed state, redirects to frontend)
  - `GET /auth/integrations` (list connected providers)
  - `DELETE /auth/integrations/{provider}` (real revoke + delete)

### Step 6 — Tech Lead agent

- `RepoScanner`: walk + filter (exclusion lists for dirs/files/binaries), budget enforcement (max 60 files, 60KB/file, 600KB total), priority files first (README, pyproject.toml)
- `MultiRepoContextMerger`: single LLM call (role=tech_lead, opus, temp=0.2), strict JSON schema in prompt, code-fence stripping on parse
- `TechLeadAgent`: token resolve via session_scope, clone all repos to task-scoped tmp dir, scan, merge, return `{repos, context, events}`
- DI: all deps via constructor with singleton defaults; tests pass mocks

### Step 7 — Remaining dev team agents

- `ArchitectAgent`: reads context + task, produces JSON plan (rationale, changes per repo, execution order, risks)
- `SeniorDeveloperAgent`: reads plan + files from disk, LLM generates code, `DiffParser` extracts `<file>` tags, writes to disk
- `DiffParser` class: regex extraction of tagged file blocks into `ParsedChange` dataclasses
- `CodeReviewerAgent`: reviews diffs against plan, returns verdict (approve/request_changes) + feedback
- `QAEngineerAgent`: runs `SandboxRunner` (pytest) per repo, LLM analyzes failures, returns verdict (pass/fail)
- `ReleaseManagerAgent`: creates branch `clyde/{task_id[:8]}/{repo}`, git add/commit/push, LLM generates PR title+body, creates PR via GitProvider
- Pipeline kickoff wired in `TaskService.create()`: builds initial state from project repos, fires `asyncio.create_task(_run_pipeline_background())`. Background runner transitions task to AWAITING_CI / COMPLETED / NEEDS_HUMAN / FAILED on exit.

### Step 8 — WebSocket streaming

- `EventBroadcaster` class: in-memory pub/sub per task, multiple subscribers, sentinel-based stream close
- WS endpoint: `WS /ws/tasks/{task_id}?token=<jwt>` — JWT auth, task ownership check, streams JSON events
- `TaskService._run_pipeline_background` publishes every event to broadcaster; sends final status/failure event before closing

### Step 9 — Memory layer

- `EmbeddingClient`: Voyage AI SDK wrapper, batch embed, SHA-256 LRU cache (1000 entries), retry policy
- `CodeChunker`: AST-based split for Python (FunctionDef/ClassDef/module), sliding window for other languages (~800 tokens, 100 overlap)
- `MemoryRepository`: pgvector cosine distance queries for episodes and code chunks, bulk insert, re-indexing
- `EpisodicMemory`: save (embed + persist) + recall (embed + vector search)
- `SemanticMemory`: index_repo (chunk + batch embed + save) + search
- `MemoryManager` facade: parallel `get_context()` via asyncio.gather, delegates to episodic + semantic
- `MemoryContext` dataclass (episodes + code_chunks)
- DI: `EmbeddingClientDep`, `MemoryRepositoryDep`, `MemoryManagerDep`

### Step 10 — Webhook flow

- `WebhookService`: HMAC-SHA256 signature verification, branch name parsing (`clyde/{task_id[:8]}/...`), CI event routing
- `DevOpsEngineerAgent`: fetches CI logs, reads changed files, LLM diagnoses + produces fix, applies via DiffParser, commits + pushes to same branch
- `POST /webhooks/github` endpoint: raw body read for HMAC, dispatches workflow_run events
- CI fix loop: failure + attempt < 3 spawns DevOps agent (runs outside LangGraph), pushes fix, transitions to AWAITING_CI; 3 failures transitions to NEEDS_HUMAN

### Step 11 — Tests

- `conftest.py`: test_settings, MockLLMGateway (records calls), MockGitProvider, make_test_jwt(), sample_repo fixture
- 12 unit test files (~67 tests): auth, retry, crypto, model router, repo scanner, code chunker, diff parser, agent registry, review/test routers, broadcaster, webhook service
- 1 integration test file (6 tests): health, auth enforcement, project/task CRUD (DB-dependent marked @integration)
- 1 agent test file (5 tests): TechLeadAgent with full mock stack

## API surface summary

### REST endpoints (all under `/api/v1`)

| Method | Path | Auth | Description |
|---|---|---|---|
| GET | `/health` | No | Liveness check |
| GET | `/ready` | No | DB connectivity check |
| GET | `/auth/oauth/{provider}/start` | JWT | Returns OAuth authorization URL |
| GET | `/auth/oauth/{provider}/callback` | State token | Exchanges code for token, redirects to frontend |
| GET | `/auth/integrations` | JWT | List connected OAuth providers |
| DELETE | `/auth/integrations/{provider}` | JWT | Revoke token at provider and delete locally |
| POST | `/projects` | JWT | Create project (optional inline repos) |
| GET | `/projects` | JWT | List projects (paginated, with repo count) |
| GET | `/projects/{id}` | JWT | Get project detail with repos |
| PATCH | `/projects/{id}` | JWT | Update project name/description |
| DELETE | `/projects/{id}` | JWT | Delete project (cascade) |
| POST | `/projects/{id}/repos` | JWT | Attach repository |
| DELETE | `/projects/{id}/repos/{repo_id}` | JWT | Detach repository |
| POST | `/tasks` | JWT | Create task and start pipeline |
| GET | `/tasks` | JWT | List tasks (filter by project/status, paginated) |
| GET | `/tasks/{id}` | JWT | Get task detail with state |
| POST | `/webhooks/github` | HMAC | GitHub CI webhook handler |

### WebSocket

| Path | Auth | Description |
|---|---|---|
| WS `/ws/tasks/{task_id}?token=<jwt>` | JWT via query | Stream pipeline events in real time |

## Database schema (2 migrations)

| Table | Key columns | Purpose |
|---|---|---|
| `projects` | id, user_id, name | User-owned project |
| `project_repos` | project_id, provider, url | Attached git repositories |
| `tasks` | project_id, user_id, status, state JSONB, pr_urls JSONB | Pipeline execution unit |
| `episodes` | task_id, user_id, embedding Vector(1024) | Past task memory |
| `code_chunks` | project_id, repo_id, embedding Vector(1024) | Indexed code for semantic search |
| `user_oauth_credentials` | user_id, provider, token_encrypted | Per-user OAuth tokens (Fernet) |
| `agents` | user_id, slug, system_prompt | M2 stub: custom agents |
| `tools` | user_id, slug, endpoint, credentials_encrypted | M2 stub: MCP connections |
| `pipelines` | user_id, slug, graph JSONB | M2 stub: custom pipelines |

Plus LangGraph checkpoint tables (created by `AsyncPostgresSaver.setup()`, not managed by Alembic).

## Empty files (intentionally deferred)

| File | Reason |
|---|---|
| `src/api/routes/agents.py` | M2 stub (Step 12) |
| `src/api/routes/pipelines.py` | M2 stub (Step 12) |
| `src/api/routes/tools.py` | M2 stub (Step 12) |
| `src/api/schemas/agent_schemas.py` | M2 stub (Step 12) |
| `src/api/schemas/pipeline_schemas.py` | M2 stub (Step 12) |
| `src/services/memory_service.py` | Superseded by `src/memory/manager.py` |
| `src/services/pipeline_service.py` | Superseded by `src/engine/graph_builder.py` + `executor.py` |
| `src/tools/mcp/*` | MCP integration deferred beyond M1 |

## Known issues and technical debt

1. **No venv created yet** — `make install-dev` has not been run. Actual import-time and runtime validation is pending.
2. **Migrations not tested against real DB** — `make migrate` needs to run against the dev RDS instance.
3. **LangGraph 1.x API unverified** — imports and method signatures are based on documented API; actual SDK behavior at version 1.1.6 needs runtime validation.
4. **Anthropic SDK 0.95** — same story; the `messages.stream()` context manager API needs runtime confirmation.
5. **Voyage AI SDK** — `EmbeddingClient` wraps sync `voyageai.Client` via `to_thread`; confirm the SDK ships with Python 3.12 compatibility.
6. **Token in state boundary** — OAuth tokens intentionally stay out of LangGraph state (fetched via DB at agent execute time), but CI logs (potentially sensitive) do enter state via DevOps agent.
7. **Tmp directory cleanup** — cloned repos in `/tmp/clyde_task_*` are never cleaned up. Should add cleanup in Release Manager or task lifecycle.
8. **No rate limiting** — API endpoints have no rate limiting beyond what Nginx/ALB provides.
9. **`services/memory_service.py` and `services/pipeline_service.py`** — listed in Brief but superseded by `memory/manager.py` and `engine/` respectively. Can delete.
10. **Docker sandbox on Fargate** — `DockerSandboxRunner` requires docker socket access; Fargate does not provide it. Need E2B runner or ECS-on-EC2 for production.

## Next steps

### Step 12 — M2 stubs (small)

Fill the empty route + schema files with minimal endpoints that return 501 Not Implemented. Documents the M2 API contract without implementing business logic.

Files to write:
- `src/api/schemas/agent_schemas.py` — AgentCreate, AgentRead, AgentUpdate
- `src/api/schemas/pipeline_schemas.py` — PipelineCreate, PipelineRead
- `src/api/routes/agents.py` — CRUD returning 501
- `src/api/routes/pipelines.py` — CRUD returning 501
- `src/api/routes/tools.py` — CRUD returning 501
- Wire into app.py

### Step 13 — Production polish (medium)

1. **CI/CD pipeline**: GitHub Actions workflow — lint (ruff), type-check (mypy), test (pytest unit), build Docker image, push to ECR
2. **Sentry integration**: wire `sentry_sdk.init()` in `Application.build()` when `SENTRY_DSN` is set
3. **Prometheus metrics**: request count, latency histogram, pipeline duration, LLM token usage — expose on `/metrics`
4. **Structured logging**: verify structlog JSON output works with CloudWatch Logs Insights
5. **AWS deployment docs**: ECR + ECS task definition, RDS parameter group for pgvector, security groups, IAM roles, ALB target group config
6. **Health check refinement**: `/ready` should also check checkpointer pool connectivity
7. **Graceful shutdown**: ensure in-flight pipelines are not killed on deploy (drain timeout on ECS)

### Post-M1 improvements (not in scope but worth tracking)

- **Local user auth**: FastAPI-managed register/login for development without DRF dependency
- **Parallel repo cloning**: `asyncio.gather` in Tech Lead for faster startup on multi-repo tasks
- **Two-pass Tech Lead**: per-repo summarize first, then cross-repo merge (better for large repos)
- **Streaming LLM responses**: agents use `stream()` instead of `chat()` for real-time UI updates
- **Memory integration into agents**: Tech Lead and Architect call `MemoryManager.get_context()` before planning
- **E2B sandbox runner**: for Fargate-compatible serverless sandbox execution
- **MCP server integration**: connect agents to external tools via Model Context Protocol
