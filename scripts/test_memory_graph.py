#!/usr/bin/env python3
"""End-to-end memory graph test — runs the real OrchestratorAgent and verifies
that the memory write + read path works correctly.

What it does:
  1. Seeds DB with test agent + project pointing at FinanceAppDesktop
  2. Seeds a GitHub credential for user_id=1 (local Docker postgres is fresh)
  3. Runs OrchestratorAgent with a security/bug-finding task
  4. Verifies memory_nodes and memory_edges were populated
  5. Runs MemoryRetrieval to confirm the read path works
  6. Prints pass/fail summary; exits 1 if any assertion failed

Usage:
    docker compose -f docker-compose-local.yaml run --rm app \\
        python scripts/test_memory_graph.py --github-token ghp_YOUR_TOKEN
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import UTC, datetime

from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Break circular import:
# base_agent → src.services.__init__ → TaskService → OrchestratorAgent → BaseAgent (not ready)
# Pre-warming task_service first so the package is in sys.modules before agents touch it.
import src.services.task_service  # noqa: F401, E402

# ── test config ───────────────────────────────────────────────────────────────

TEST_USER_ID = 1
TEST_TASK_ID = f"mem-test-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"

TARGET_REPO      = "https://github.com/DanyloTs/FinanceAppDesktop"
TARGET_BRANCH    = "main"

TASK_DESCRIPTION = (
    "Analyze this codebase and find the single most critical security vulnerability "
    "or bug. Explain what it is, where it lives (file + line), why it is dangerous, "
    "and what the fix looks like."
)


# ── pretty output ─────────────────────────────────────────────────────────────

class _Log:
    _passed = 0
    _failed = 0

    @staticmethod
    def section(title: str) -> None:
        print()
        print("=" * 60)
        print(f"  {title}")
        print("=" * 60)

    @classmethod
    def ok(cls, label: str) -> None:
        cls._passed += 1
        print(f"  [PASS] {label}")

    @classmethod
    def fail(cls, label: str, detail: str = "") -> None:
        cls._failed += 1
        print(f"  [FAIL] {label}")
        if detail:
            for line in str(detail).splitlines()[:5]:
                print(f"         {line}")

    @classmethod
    def info(cls, label: str) -> None:
        print(f"  [INFO] {label}")

    @classmethod
    def skip(cls, label: str, reason: str = "") -> None:
        print(f"  [SKIP] {label}" + (f" — {reason}" if reason else ""))

    @classmethod
    def check(cls, label: str, condition: bool, detail: str = "") -> None:
        if condition:
            cls.ok(label)
        else:
            cls.fail(label, detail)

    @classmethod
    def summary(cls) -> int:
        print()
        print("=" * 60)
        total = cls._passed + cls._failed
        print(f"  Results: {cls._passed}/{total} passed")
        if cls._failed:
            print(f"  {cls._failed} assertion(s) FAILED")
        print("=" * 60)
        print()
        return 1 if cls._failed else 0


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _connect():
    import asyncpg
    return await asyncpg.connect(
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "5432")),
        database=os.getenv("DB_NAME", "clyde"),
        user=os.getenv("DB_USER", "clyde"),
        password=os.getenv("DB_PASSWORD", "clyde"),
    )


async def seed_db(github_token: str) -> tuple[str, str]:
    """Seed GitHub credential, agent, project, repo. Returns (agent_id, project_id)."""
    conn = await _connect()
    try:
        # ── GitHub credential (credentials table, kind=oauth) ─────────────────
        fernet_key = os.getenv("FERNET_KEY", "")
        if not fernet_key:
            # generate a throwaway key — only valid for this run
            fernet_key = Fernet.generate_key().decode()
            _Log.info("FERNET_KEY not in .env — generated a one-time key")
        payload_json = json.dumps({"access_token": github_token, "refresh_token": None})
        encrypted = Fernet(fernet_key.encode()).encrypt(payload_json.encode()).decode()
        metadata = json.dumps({
            "provider": "github",
            "scopes": ["repo"],
            "expires_at": None,
            "needs_reauth": False,
            "raw": {},
        })
        await conn.execute(
            """
            INSERT INTO credentials
                (id, user_id, kind, label, encrypted_payload, preview,
                 metadata_json, deleted_at, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'oauth', 'github', $2,
                    'oauth:github [repo]', $3::jsonb, NULL, now(), now())
            ON CONFLICT DO NOTHING
            """,
            TEST_USER_ID,
            encrypted,
            metadata,
        )
        # If there was already a credential, update the token so it's valid
        await conn.execute(
            """
            UPDATE credentials
            SET encrypted_payload = $1, updated_at = now()
            WHERE user_id = $2 AND kind = 'oauth'
              AND (metadata_json->>'provider') = 'github'
              AND deleted_at IS NULL
            """,
            encrypted,
            TEST_USER_ID,
        )
        _Log.info(f"GitHub credential seeded for user_id={TEST_USER_ID}")

        # ── subagent ──────────────────────────────────────────────────────────
        subagent_id: str = await conn.fetchval(
            "SELECT id::text FROM subagents WHERE name = 'code-implementer' LIMIT 1"
        )
        if not subagent_id:
            subagent_id = str(uuid.uuid4())
            await conn.execute(
                """
                INSERT INTO subagents
                    (id, name, display_name, description, system_prompt,
                     model, sort_order, is_active, created_at, updated_at)
                VALUES ($1, 'code-implementer', 'Code Implementer',
                        'Implementation worker', 'You are a senior engineer.',
                        'claude-sonnet-4-6', 0, true, now(), now())
                ON CONFLICT (name) DO NOTHING
                """,
                uuid.UUID(subagent_id),
            )
            subagent_id = await conn.fetchval(
                "SELECT id::text FROM subagents WHERE name = 'code-implementer' LIMIT 1"
            )
        _Log.info(f"Subagent: code-implementer ({subagent_id[:8]}…)")

        # ── agent ─────────────────────────────────────────────────────────────
        agent_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO agents
                (id, user_id, name, display_name, description,
                 system_prompt, model, is_default, is_active, created_at, updated_at)
            VALUES ($1, $2, 'mem-test-orchestrator', 'Memory Test Orchestrator',
                    'Created by test_memory_graph.py',
                    NULL, NULL, false, true, now(), now())
            ON CONFLICT (user_id, name) DO NOTHING
            """,
            uuid.UUID(agent_id),
            TEST_USER_ID,
        )
        agent_id = await conn.fetchval(
            "SELECT id::text FROM agents WHERE user_id=$1 AND name='mem-test-orchestrator'",
            TEST_USER_ID,
        )
        _Log.info(f"Agent: mem-test-orchestrator ({agent_id[:8]}…)")

        # ── agent_subagents link ──────────────────────────────────────────────
        await conn.execute(
            """
            INSERT INTO agent_subagents
                (id, agent_id, subagent_id, sort_order, is_active, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, $2, 0, true, now(), now())
            ON CONFLICT (agent_id, subagent_id) DO NOTHING
            """,
            uuid.UUID(agent_id),
            uuid.UUID(subagent_id),
        )

        # ── project + repo ────────────────────────────────────────────────────
        project_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO projects
                (id, user_id, name, description, created_at, updated_at)
            VALUES ($1, $2, 'mem-test-finance-app',
                    'FinanceAppDesktop — memory graph test', now(), now())
            ON CONFLICT DO NOTHING
            """,
            uuid.UUID(project_id),
            TEST_USER_ID,
        )
        project_id = await conn.fetchval(
            "SELECT id::text FROM projects WHERE user_id=$1 AND name='mem-test-finance-app'",
            TEST_USER_ID,
        )
        await conn.execute(
            """
            INSERT INTO project_repos
                (id, project_id, provider, url, default_branch, created_at, updated_at)
            VALUES (gen_random_uuid(), $1, 'github', $2, $3, now(), now())
            ON CONFLICT DO NOTHING
            """,
            uuid.UUID(project_id),
            TARGET_REPO,
            TARGET_BRANCH,
        )
        _Log.info(f"Project: mem-test-finance-app → {TARGET_REPO}")

        return agent_id, project_id
    finally:
        await conn.close()


async def count_memory_rows(task_id: str) -> tuple[int, int, int]:
    """Return (task_node_exists, action_node_count, edge_count) for this task.

    Only the task node has task_id in properties. Action nodes are linked via
    'executed' edges FROM the task node, so we count those separately.
    """
    conn = await _connect()
    try:
        task_node_id = await conn.fetchval(
            "SELECT id FROM memory_nodes WHERE properties->>'task_id' = $1",
            task_id,
        )
        if not task_node_id:
            return 0, 0, 0
        action_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_edges WHERE source_id = $1 AND edge_type = 'executed'",
            task_node_id,
        )
        edge_count = await conn.fetchval(
            "SELECT COUNT(*) FROM memory_edges WHERE source_id = $1",
            task_node_id,
        )
        return 1, int(action_count), int(edge_count)
    finally:
        await conn.close()


# ── main ──────────────────────────────────────────────────────────────────────

async def run_tests(github_token: str) -> int:
    from src.agents.team.orchestrator_agent import OrchestratorAgent
    from src.memory.retrieval import MemoryRetrieval

    # ── Phase 0: seed ─────────────────────────────────────────────────────────
    _Log.section("Phase 0 — Seed DB")
    try:
        agent_id, project_id = await seed_db(github_token)
        _Log.ok("DB seeded")
    except Exception as exc:
        _Log.fail("DB seed failed", str(exc))
        return _Log.summary()

    # ── Phase 1: run orchestrator ─────────────────────────────────────────────
    _Log.section("Phase 1 — OrchestratorAgent (security scan)")
    _Log.info(f"Task ID : {TEST_TASK_ID}")
    _Log.info(f"Repo    : {TARGET_REPO}")
    _Log.info(f"Prompt  : {TASK_DESCRIPTION[:80]}…")
    print()

    state: dict = {
        "task_id": TEST_TASK_ID,
        "user_id": TEST_USER_ID,
        "agent_id": agent_id,
        "project_id": project_id,
        "description": TASK_DESCRIPTION,
        "repos": [{"url": TARGET_REPO, "default_branch": TARGET_BRANCH}],
        "attempt": 0,
        "events": [],
    }

    orchestrator_ok = False
    orchestrator_result: dict = {}
    try:
        agent = OrchestratorAgent()
        orchestrator_result = await agent(state)
        orchestrator_ok = True
        _Log.ok("OrchestratorAgent completed without exception")
    except Exception as exc:
        _Log.fail("OrchestratorAgent raised an exception", str(exc))

    summary = orchestrator_result.get("context", {}).get("summary", "")
    if summary:
        print()
        print("  Agent summary:")
        print()
        for line in summary[:2000].splitlines():
            print(f"    {line}")
        print()

    # ── Phase 2: memory write path ────────────────────────────────────────────
    _Log.section("Phase 2 — Memory write path verification")

    task_exists, action_count, edge_count = await count_memory_rows(TEST_TASK_ID)
    _Log.info(f"task node created      : {bool(task_exists)}")
    _Log.info(f"action nodes recorded  : {action_count}")
    _Log.info(f"edges from task node   : {edge_count}")

    _Log.check("task node created", bool(task_exists))
    if orchestrator_ok:
        _Log.check(
            "action nodes recorded (tool calls captured)",
            action_count > 0,
            "No action nodes — tool calls not recorded",
        )
        _Log.check("edges created between nodes", edge_count > 0, "No edges found")
    else:
        _Log.skip("action nodes / edges", "orchestrator did not run successfully")

    # ── Phase 3: memory read path ─────────────────────────────────────────────
    _Log.section("Phase 3 — Memory read path (MemoryRetrieval)")

    r = MemoryRetrieval()

    # list_recent includes both completed and failed tasks
    recent_result = await r.list_recent(user_id=TEST_USER_ID, limit=3)
    _Log.check(
        "list_recent() finds the task",
        any(w in recent_result.lower() for w in ["security", "vulnerability", "analyze"]),
        recent_result[:200],
    )

    if orchestrator_ok:
        # recall() only searches completed tasks — only meaningful after a successful run
        recall_result = await r.recall(
            user_id=TEST_USER_ID,
            query="security vulnerability bug codebase",
        )
        _Log.check(
            "recall() finds the completed task",
            "No relevant past tasks found" not in recall_result,
            recall_result[:200],
        )
        _Log.check(
            "recall() result contains task description keywords",
            any(w in recall_result.lower() for w in ["security", "vulnerability", "finance"]),
            recall_result[:300],
        )
        print()
        print("  Memory recall result:")
        print()
        for line in recall_result.splitlines():
            print(f"    {line}")
        print()
    else:
        _Log.skip("recall()", "orchestrator failed — task status is 'failed', not 'completed'")

    from src.db.session import db
    await db.dispose()
    return _Log.summary()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Memory graph E2E test against DanyloTs/FinanceAppDesktop"
    )
    parser.add_argument(
        "--github-token",
        default=os.getenv("GITHUB_TOKEN", ""),
        help="GitHub PAT with repo read access (or set GITHUB_TOKEN env var)",
    )
    args = parser.parse_args()

    if not args.github_token:
        print("ERROR: --github-token is required (or set GITHUB_TOKEN env var)")
        print("       Local Docker postgres has no credentials — need to seed one.")
        sys.exit(1)

    print()
    print(f"Memory Graph E2E Test — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M:%S UTC')}")
    exit_code = asyncio.run(run_tests(args.github_token))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
