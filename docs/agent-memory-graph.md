# Agent Memory — Graph-Based Cross-Task Context

## Problem

Orchestrators have no memory between tasks. Each task starts with a blank slate — the same orchestrator that fixed a bug in `payments_service.py` last week has no recollection of it when asked to extend that file today. Sub-agents are even more isolated: each runs in a fresh SDK session with no shared context.

---

## Approaches Overview

| | Option A — Prompt Injection | Option B — Memory MCP |
|---|---|---|
| **Summary** | Pipeline silently records tool calls and injects past context into the system prompt before each task | Same graph exposed as MCP tools the orchestrator actively calls mid-task |
| **Who controls retrieval** | Infrastructure | The agent itself |
| **Build first?** | Yes | After Option A, once graph has real data |

The write path (recording tool calls into the graph) is **identical** for both options. Only the read path differs.

---

## Shared Foundation

Everything in this section applies to both options.

### Graph Schema

Two universal tables store everything.

```sql
CREATE TABLE nodes (
    id          BIGSERIAL PRIMARY KEY,
    node_type   VARCHAR(50)  NOT NULL,          -- 'task', 'action', 'entity'
    properties  JSONB        NOT NULL DEFAULT '{}',
    embedding   VECTOR(1536),                   -- populated for 'task' nodes only
    search_text TSVECTOR GENERATED ALWAYS AS (
                    to_tsvector('english',
                        coalesce(properties->>'description', '') || ' ' ||
                        coalesce(properties->>'tool_name',   '') || ' ' ||
                        coalesce(properties->>'identifier',  ''))
                ) STORED,
    created_at  TIMESTAMPTZ  DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE INDEX idx_nodes_type        ON nodes(node_type);
CREATE INDEX idx_nodes_props       ON nodes USING GIN(properties);
CREATE INDEX idx_nodes_search_text ON nodes USING GIN(search_text);
CREATE INDEX idx_nodes_embedding   ON nodes USING hnsw (embedding vector_cosine_ops);

CREATE TABLE edges (
    id          BIGSERIAL PRIMARY KEY,
    source_id   BIGINT      NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    target_id   BIGINT      NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    edge_type   VARCHAR(50) NOT NULL,   -- 'executed', 'read', 'wrote', 'called', 'targeted'
    weight      FLOAT       DEFAULT 1.0,
    properties  JSONB       DEFAULT '{}',
    created_at  TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(source_id, target_id, edge_type)
);

CREATE INDEX idx_edges_source ON edges(source_id, edge_type);
CREATE INDEX idx_edges_target ON edges(target_id, edge_type);
CREATE INDEX idx_edges_type   ON edges(edge_type);
```

### Node Types

**`task`** — one per task run. Also carries an `embedding` for semantic retrieval.
```json
{
  "task_id":      "uuid of the tasks table row",
  "user_id":      123,
  "agent_id":     "uuid",
  "description":  "fix the auth bug in payments_service.py",
  "status":       "completed | failed",
  "attempt":      1,
  "completed_at": "2026-05-06T12:00:00Z"
}
```

**`action`** — one per tool invocation. Outcome is patched in when `tool_result` arrives.
```json
{
  "tool_name":   "Read",
  "tool_use_id": "toolu_01...",
  "turn":        3,
  "detail":      "Read src/payments/service.py",
  "outcome":     "success | error",
  "is_error":    false
}
```

**`entity`** — upserted by `(node_type, properties->>'identifier')`, never duplicated across tasks.

| entity kind | identifier | example |
|---|---|---|
| `file` | repo-relative path | `src/payments/service.py` |
| `repo` | `owner/repo` | `intrepideai/clyde_ai` |
| `api` | MCP provider name | `github`, `jira`, `slack` |
| `subagent` | subagent name | `code-implementer` |
| `channel` | Slack channel name | `#eng-alerts` |

### Edge Types

```
task    ──executed──>    action
action  ──read──>        entity:file
action  ──wrote──>       entity:file
action  ──called──>      entity:api
action  ──called──>      entity:subagent
action  ──targeted──>    entity:repo
```

The `weight` increments each time the same entity is touched within a task — a file read 5 times carries more signal than one read once.

### Write Path — Real-Time During Execution

Writes happen at four moments. The `GraphWriter` service wraps every DB operation in `try/except` — any failure is logged and swallowed, never propagating to the task pipeline.

| Moment | Where in code | What gets written |
|---|---|---|
| Task starts | `OrchestratorAgent.execute()` before `run_sdk_session` | `task` node (status: running, embedding computed) |
| Tool fires | `SDKAgent._log_assistant_message()` alongside `tool_call` log | `action` node + edge to task + upsert entity nodes + edges |
| Tool returns | `SDKAgent._log_tool_results()` alongside `tool_result` log | patch `action` node: outcome, is_error |
| Task ends/fails | `BaseAgent.__call__()` lifecycle wrapper | update `task` node: status, completed_at |

### Entity Extraction

`EntityExtractor` is a pure function — no DB dependency, fully testable. Maps `(tool_name, tool_input_dict)` to a list of `(entity_kind, identifier)` pairs. One tool call can yield multiple entities.

| Tool pattern | Entity kind | Identifier source |
|---|---|---|
| `Read`, `Edit`, `Write` | `file` | `tool_input["file_path"]` |
| `Glob`, `Grep` | `file` | `tool_input["path"]` |
| `Bash(git diff*)` | `repo` | working directory |
| `Agent` | `subagent` | `tool_input["agent_name"]` |
| `mcp__github__*` | `api:github` | always |
| `mcp__github__*` | `repo` | `tool_input["owner"]`/`tool_input["repo"]` if present |
| `mcp__jira__*` | `api:jira` | always |
| `mcp__slack__*` | `api:slack` | always |
| `mcp__slack__*` | `channel` | `tool_input["channel"]` if present |

### GraphWriter Interface (logical)

```
GraphWriter
  create_task_node(task_id, user_id, agent_id, description, attempt) → node_id
  create_action_node(task_node_id, tool_name, tool_use_id, turn, detail) → node_id
  patch_action_outcome(tool_use_id, outcome, is_error)
  upsert_entity(kind, identifier) → node_id
  create_edge(source_id, target_id, edge_type, weight)
  finish_task(task_node_id, status)
```

Instantiated at the start of `run_sdk_session`, receives `task_id` + `user_id` via a `graph_context` dict alongside the existing `mcp_context`.

### Why Hybrid Search (Not Pure Vector)

| Query type | What it catches | Example |
|---|---|---|
| Vector (semantic) | Conceptually similar tasks | "fix auth" matches "resolve login issue" |
| Full-text (tsvector) | Exact names and codes | `payments_service.py`, `mcp__github__`, error codes |
| Graph expansion | Relational context | files co-touched, tools co-used, outcomes |

Pure vector misses exact file/API name matches. Pure full-text misses paraphrased tasks. The `search_text` generated column covers task descriptions, tool names, and entity identifiers under a single GIN index.

---

## Option A — Automatic Prompt Injection

**How it works:** The pipeline runs a hybrid search before every task, expands the top results through the graph, formats the output as a memory block, and prepends it to the orchestrator system prompt. The agent has zero awareness of this — it simply receives a richer prompt.

### Read Path

**Step 1 — Vector leg**
```sql
SELECT id, 1 - (embedding <=> :query_embedding) AS score
FROM nodes
WHERE node_type = 'task'
  AND properties->>'user_id' = :user_id
  AND properties->>'status'  = 'completed'
ORDER BY embedding <=> :query_embedding
LIMIT 20
```

**Step 2 — Full-text leg**
```sql
SELECT id, ts_rank(search_text, plainto_tsquery('english', :query_text)) AS score
FROM nodes
WHERE node_type   = 'task'
  AND properties->>'user_id' = :user_id
  AND search_text @@ plainto_tsquery('english', :query_text)
ORDER BY score DESC
LIMIT 20
```

**Step 3 — RRF merge**
```
rrf_score(doc) = 1 / (60 + rank_vector) + 1 / (60 + rank_fulltext)
```
Take top-5 by combined score.

**Step 4 — Graph expansion (2 hops)**
```sql
-- Hop 1: task → actions
SELECT 'action' AS kind, a.properties
FROM edges e1
JOIN nodes a ON a.id = e1.target_id
WHERE e1.source_id = ANY(:task_node_ids)
  AND e1.edge_type = 'executed'

UNION ALL

-- Hop 2: task → actions → entities
SELECT n.node_type AS kind, n.properties
FROM edges e1
JOIN edges e2 ON e2.source_id = e1.target_id
JOIN nodes  n  ON n.id = e2.target_id
WHERE e1.source_id = ANY(:task_node_ids)
  AND e1.edge_type = 'executed'
  AND e2.edge_type IN ('read', 'wrote', 'called', 'targeted')
```

**Step 5 — Memory block injected into system prompt**
```
=== Relevant memory from past tasks ===

[Task: "fix the auth bug in payments_service.py" — completed]
  Actions: Read(src/payments/service.py), Edit(src/payments/service.py), Edit(src/config/auth.py)
  Entities touched: src/payments/service.py, src/config/auth.py, api:github
  Outcome: 2 files changed, PR created

[Task: "add rate limiting to payments endpoint" — completed]
  Actions: Read(src/payments/service.py), Read(src/middleware/rate_limit.py), Edit(src/payments/service.py)
  Entities touched: src/payments/service.py, src/middleware/rate_limit.py
  Outcome: 1 file changed, PR created

=======================================
```

### Implementation Steps

1. Graph migration — `nodes` + `edges` tables, generated `search_text` column, HNSW index
2. `EntityExtractor` — pure function, unit-tested
3. `GraphWriter` — async, failure-isolated
4. Wire write path — 4 trigger points in `run_sdk_session` + `execute()`
5. Retrieval query — hybrid search + RRF + graph expansion
6. Memory block formatter
7. Inject into prompt — `OrchestratorAgent.execute()` before `run_sdk_session`

---

## Option B — Memory MCP Server

**How it works:** The same graph is exposed as an MCP server. The orchestrator actively calls `mcp__memory__*` tools mid-task, on demand — exactly like it calls GitHub or Jira. Memory retrieval is a conscious agent decision, not a pipeline side-effect.

### Architecture

A new `memory.py` factory lives alongside `src/agent_tools/mcp/github.py`, `jira.py`, etc. The orchestrator gets `mcp__memory__*` in its allowed tool list via the standard DB config — no special casing in the pipeline.

### Exposed MCP Tools

**`mcp__memory__recall`** — primary retrieval. Runs full hybrid search + RRF + graph expansion internally, returns a formatted summary.
```json
input:  { "query": "string", "limit": 5 }
output: formatted memory block string (same as Option A's injected block)
```

**`mcp__memory__search_entity`** — entity history. Find all past tasks that touched a specific file, repo, or API. Useful when the agent opens a file and wants its history.
```json
input:  { "kind": "file | repo | api | subagent", "identifier": "src/payments/service.py" }
output: list of past tasks with descriptions and outcomes
```

**`mcp__memory__annotate`** — freeform note. Lets the orchestrator record a decision not captured by tool calls (e.g. "chose not to refactor X because Y").
```json
input:  { "note": "string" }
output: confirmation — appended to task node's properties.notes array
```

**`mcp__memory__list_recent`** — recent tasks. Orientation tool for the start of a session.
```json
input:  { "limit": 10 }
output: list of task descriptions + statuses + timestamps
```

### Orchestrator Prompt Addition

One line added to the orchestrator base prompt:

```
Before starting work on any task, call mcp__memory__recall with the task
description to check for relevant prior context — files previously touched,
tools previously used, and past outcomes.
```

Without this, the agent may never discover or use the tool.

### Fits Existing MCP Architecture

| Concern | How it works |
|---|---|
| Server registration | `memory.py` factory in `src/agent_tools/mcp/` |
| DB config | New `mcp_server_configs` row with `provider_name = "memory"` |
| Per-agent activation | Admin links via `subagent_tools`, same as GitHub |
| Auth | No OAuth — server connects directly to app DB |
| Transport | `stdio` (in-process) — no network hop |

### Implementation Steps

1. Graph migration — same as Option A (shared write path)
2. `EntityExtractor` + `GraphWriter` — same as Option A
3. Wire write path — same as Option A
4. `MemoryMCPServer` — MCP server with the 4 tools above
5. Retrieval logic — hybrid search + RRF + graph expansion inside MCP handlers
6. DB config seed — `mcp_server_configs` row for `memory` provider
7. Prompt addition — one-line instruction in orchestrator base prompt

---

## Comparison

| | Option A — Prompt Injection | Option B — Memory MCP |
|---|---|---|
| **Who controls retrieval** | Pipeline (infrastructure) | Orchestrator (agent) |
| **When retrieval happens** | Once, at task start | Any time, agent decides |
| **Agent awareness** | None — memory is invisible | Full — agent sees tool result |
| **Query flexibility** | Fixed: embed task description | Flexible: agent chooses query |
| **Mid-task context** | Not possible | Yes — agent queries as it discovers files |
| **Complexity** | Lower | Higher (MCP server + DB config) |
| **Risk** | Bad retrieval silently pollutes prompt | Agent may not call the tool |
| **Fits existing arch** | Pipeline state patch pattern | MCP provider pattern |

### Recommended Approach

Use both — the write path is identical so there is no extra cost. For the read path:

- **Build Option A first** — lower risk, immediately useful, no agent behaviour change required
- **Add Option B after** — once the graph is populated with real data and retrieval quality is validated, the MCP server adds dynamic mid-task recall the agent controls itself
