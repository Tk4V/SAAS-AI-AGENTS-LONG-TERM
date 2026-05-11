#!/usr/bin/env python3
"""Test script for the human-in-the-loop task approval flow.

Runs two test suites:

  Part 1 — Unit: tests `permission_gate.py` entirely in-process (no app/DB).
            Verifies asyncio.Event coordination: register → resolve → wait.

  Part 2 — Integration: tests the HTTP API endpoints against a running app.
            Creates a project + task, watches for AWAITING_APPROVAL, then
            resolves every pending approval (approve or deny per --action).

Prerequisites for Part 2:
    1. App running: docker compose -f docker-compose-local.yaml up --build
    2. DB migration applied: alembic upgrade head
    3. User with the configured user_id has a GitHub credential in the DB.

Usage:
    # Run only the unit tests (no app needed)
    python scripts/test_approval_flow.py --unit-only

    # Full flow — approve every pending approval
    python scripts/test_approval_flow.py \\
        --project-id <uuid>          # reuse existing project
        --action approve             # or: deny

    # Full flow — create a new project with the test repo
    python scripts/test_approval_flow.py \\
        --repo https://github.com/DanyloTs/blender-microservice.git \\
        --action approve
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import jwt as pyjwt
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

POLL_INTERVAL = 3          # seconds between status polls
MAX_POLL_ATTEMPTS = 120    # 6 minutes total
TERMINAL_STATUSES = {"completed", "awaiting_ci", "needs_human", "failed"}


# ─── helpers ──────────────────────────────────────────────────────────────────

class Logger:
    @staticmethod
    def info(msg: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{ts}] {msg}")

    @staticmethod
    def ok(msg: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{ts}] \033[92m✓ {msg}\033[0m")

    @staticmethod
    def fail(msg: str) -> None:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{ts}] \033[91m✗ {msg}\033[0m")

    @staticmethod
    def section(title: str) -> None:
        print()
        print("─" * 60)
        print(f"  {title}")
        print("─" * 60)


def _jwt(config: "Config") -> str:
    now = datetime.now(UTC)
    payload: dict = {
        "user_id": config.user_id,
        "username": "test_user",
        "email": "test@local",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=1)).timestamp()),
    }
    if config.jwt_audience:
        payload["aud"] = config.jwt_audience
    return pyjwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


class Config:
    def __init__(self) -> None:
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-shared-with-django")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "")
        self.port = int(os.getenv("PORT", "8000"))
        self.api_prefix = os.getenv("API_PREFIX", "/api/v1")
        self.user_id = int(os.getenv("E2E_USER_ID", "7"))

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}{self.api_prefix}"


# ─── Part 1: unit tests for permission_gate ──────────────────────────────────

def run_unit_tests() -> bool:
    """Test permission_gate asyncio coordination without touching the DB or HTTP."""
    Logger.section("Part 1 — Unit: permission_gate.py")

    passed = 0
    failed = 0

    async def _test_approve() -> None:
        nonlocal passed, failed
        from src.agent_tools.permission_gate import cleanup, get_decision, register, resolve

        task_id = uuid4()
        approval_id = uuid4()

        event = register(task_id, approval_id)
        assert not event.is_set(), "Event should start unset"

        # Resolve from a concurrent task (simulates the HTTP endpoint)
        async def _resolve() -> None:
            await asyncio.sleep(0.05)
            found = resolve(approval_id=approval_id, approved=True)
            assert found, "resolve() should return True when approval is pending"

        await asyncio.gather(
            asyncio.create_task(_resolve()),
            asyncio.wait_for(event.wait(), timeout=2.0),
        )

        decision = get_decision(approval_id=approval_id)
        assert decision is True, f"Expected True, got {decision}"
        cleanup(task_id)
        Logger.ok("approve path: event set, decision=True")
        passed += 1

    async def _test_deny() -> None:
        nonlocal passed, failed
        from src.agent_tools.permission_gate import cleanup, get_decision, register, resolve

        task_id = uuid4()
        approval_id = uuid4()

        event = register(task_id, approval_id)

        async def _resolve() -> None:
            await asyncio.sleep(0.05)
            resolve(approval_id=approval_id, approved=False)

        await asyncio.gather(
            asyncio.create_task(_resolve()),
            asyncio.wait_for(event.wait(), timeout=2.0),
        )

        decision = get_decision(approval_id=approval_id)
        assert decision is False, f"Expected False, got {decision}"
        cleanup(task_id)
        Logger.ok("deny path: event set, decision=False")
        passed += 1

    async def _test_not_found() -> None:
        nonlocal passed, failed
        from src.agent_tools.permission_gate import resolve

        found = resolve(approval_id=uuid4(), approved=True)
        assert not found, "resolve() should return False for unknown approval_id"
        Logger.ok("not-found path: resolve() returns False for unknown id")
        passed += 1

    async def _test_cleanup() -> None:
        nonlocal passed, failed
        from src.agent_tools.permission_gate import _pending, cleanup, register

        task_id = uuid4()
        for _ in range(3):
            register(task_id, uuid4())
        assert task_id in _pending
        cleanup(task_id)
        assert task_id not in _pending, "cleanup() should remove task from _pending"
        Logger.ok("cleanup path: task removed from _pending")
        passed += 1

    async def _test_multiple_approvals() -> None:
        """Two approvals for the same task resolved in reverse order."""
        nonlocal passed, failed
        from src.agent_tools.permission_gate import cleanup, get_decision, register, resolve

        task_id = uuid4()
        id_a = uuid4()
        id_b = uuid4()
        event_a = register(task_id, id_a)
        event_b = register(task_id, id_b)

        resolve(approval_id=id_b, approved=False)
        resolve(approval_id=id_a, approved=True)

        await asyncio.wait_for(asyncio.gather(event_a.wait(), event_b.wait()), timeout=1.0)
        assert get_decision(id_a) is True
        assert get_decision(id_b) is False
        cleanup(task_id)
        Logger.ok("multiple approvals: independent decisions, correct events")
        passed += 1

    tests = [_test_approve, _test_deny, _test_not_found, _test_cleanup, _test_multiple_approvals]
    for test_fn in tests:
        try:
            asyncio.run(test_fn())
        except Exception as exc:
            Logger.fail(f"{test_fn.__name__}: {exc}")
            failed += 1

    print()
    Logger.info(f"Unit results: {passed} passed, {failed} failed")
    return failed == 0


# ─── Part 2: HTTP integration test ────────────────────────────────────────────

async def _ensure_project(
    http: httpx.AsyncClient, cfg: Config, headers: dict, repo_url: str | None, project_id: str | None,
) -> str:
    """Return an existing project_id or create one with the given repo."""
    if project_id:
        Logger.info(f"Using existing project: {project_id}")
        return project_id

    if not repo_url:
        Logger.fail("Provide --repo or --project-id for the integration test.")
        sys.exit(1)

    name = f"approval-test-{datetime.now(UTC).strftime('%H%M%S')}"
    Logger.info(f"Creating project '{name}' with {repo_url}...")
    r = await http.post(
        f"{cfg.base_url}/projects",
        headers=headers,
        json={
            "name": name,
            "description": "Approval flow test project",
            "repos": [{"url": repo_url, "default_branch": "main"}],
        },
    )
    if r.status_code != 201:
        Logger.fail(f"Project creation failed: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    pid = r.json()["id"]
    Logger.ok(f"Project created: {pid}")
    return pid


async def _create_task(
    http: httpx.AsyncClient, cfg: Config, headers: dict, project_id: str,
) -> str:
    prompt = (
        "Use the Bash tool to run `git log --oneline -5` and show the commit history. "
        "Then use Bash to run `git status` to show the current repo state. "
        "Do not use Read or Glob — use Bash directly for both commands."
    )
    Logger.info(f"Creating task: {prompt[:80]}...")
    r = await http.post(
        f"{cfg.base_url}/tasks",
        headers=headers,
        json={"project_id": project_id, "description": prompt},
    )
    if r.status_code != 201:
        Logger.fail(f"Task creation failed: {r.status_code} {r.text[:200]}")
        sys.exit(1)
    tid = r.json()["id"]
    Logger.ok(f"Task created: {tid}")
    return tid


async def _poll_and_resolve(
    http: httpx.AsyncClient, cfg: Config, headers: dict, task_id: str, action: str,
) -> str:
    """Poll task status, resolve any pending approvals, return final status."""
    Logger.info("Polling task status (resolving approvals automatically)...")
    last_status = "running"
    approvals_resolved: set[str] = set()

    for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
        await asyncio.sleep(POLL_INTERVAL)

        r = await http.get(f"{cfg.base_url}/tasks/{task_id}", headers=headers)
        if r.status_code != 200:
            continue
        task = r.json()
        status = task["status"]

        if status != last_status:
            Logger.info(f"  Status: {last_status} → {status}")
            last_status = status

        if status == "awaiting_approval":
            # Fetch and resolve pending approvals.
            ar = await http.get(
                f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers
            )
            if ar.status_code != 200:
                Logger.fail(f"GET approvals failed: {ar.status_code} {ar.text[:100]}")
                continue

            approvals = ar.json()
            pending = [a for a in approvals if a["status"] == "pending" and a["id"] not in approvals_resolved]

            if not pending:
                Logger.info("  (awaiting_approval but no new pending rows yet, retrying...)")
                continue

            for approval in pending:
                aid = approval["id"]
                tool = approval["tool_name"]
                Logger.info(f"  Pending approval: id={aid} tool={tool}")
                Logger.info(f"  Resolving with action={action}...")

                rr = await http.post(
                    f"{cfg.base_url}/tasks/{task_id}/approvals/{aid}/resolve",
                    headers=headers,
                    json={"approved": action == "approve"},
                )
                if rr.status_code == 200:
                    Logger.ok(f"  Resolved approval {aid} ({action}d)")
                    approvals_resolved.add(aid)
                else:
                    Logger.fail(f"  Resolve failed: {rr.status_code} {rr.text[:100]}")

        elif status in TERMINAL_STATUSES:
            return status

    return last_status


async def _verify_approval_endpoints(
    http: httpx.AsyncClient, cfg: Config, headers: dict, task_id: str,
) -> bool:
    """Smoke-test that list and resolve endpoints respond correctly."""
    passed = True

    # GET /tasks/{id}/approvals should return 200 (empty list is fine)
    r = await http.get(f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers)
    if r.status_code == 200:
        Logger.ok(f"GET /approvals → 200, {len(r.json())} rows")
    else:
        Logger.fail(f"GET /approvals → unexpected {r.status_code}: {r.text[:100]}")
        passed = False

    # POST resolve for a bogus UUID should return 404
    fake_id = str(uuid4())
    r = await http.post(
        f"{cfg.base_url}/tasks/{task_id}/approvals/{fake_id}/resolve",
        headers=headers,
        json={"approved": True},
    )
    if r.status_code == 404:
        Logger.ok(f"POST /approvals/<unknown>/resolve → 404 (expected)")
    else:
        Logger.fail(f"POST /approvals/<unknown>/resolve → unexpected {r.status_code}")
        passed = False

    return passed


async def run_integration_tests(args: argparse.Namespace) -> bool:
    Logger.section("Part 2 — Integration: HTTP API")

    cfg = Config()
    if args.user_id:
        cfg.user_id = args.user_id

    token = _jwt(cfg)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as http:
        # Health check
        r = await http.get(f"{cfg.base_url}/health")
        if r.status_code != 200:
            Logger.fail(f"App not responding ({r.status_code}). Is the server running?")
            return False
        Logger.ok(f"App healthy: {r.json().get('service')} v{r.json().get('version')}")

        project_id = await _ensure_project(
            http, cfg, headers, repo_url=args.repo, project_id=args.project_id
        )
        task_id = await _create_task(http, cfg, headers, project_id)

        # Smoke-test endpoints immediately (task is running, list should be empty)
        endpoints_ok = await _verify_approval_endpoints(http, cfg, headers, task_id)

        final_status = await _poll_and_resolve(http, cfg, headers, task_id, action=args.action)

        print()
        if final_status in ("completed", "awaiting_ci"):
            Logger.ok(f"Task reached terminal status: {final_status}")
        elif final_status == "failed":
            # Failure is acceptable in a test environment (no real GitHub token etc.)
            Logger.info(f"Task ended with: {final_status} — check logs for details")
        else:
            Logger.info(f"Task ended with: {final_status}")

        # Fetch final approval list
        r = await http.get(f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers)
        if r.status_code == 200:
            rows = r.json()
            Logger.info(f"Total approval rows: {len(rows)}")
            for row in rows:
                status_str = row["status"]
                color = "\033[92m" if status_str == "approved" else ("\033[91m" if status_str == "denied" else "\033[93m")
                print(f"  {color}{status_str:8s}\033[0m  {row['tool_name']:40s}  {row['id']}")

        return endpoints_ok


# ─── entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Test the human-in-the-loop task approval flow"
    )
    parser.add_argument(
        "--unit-only", action="store_true",
        help="Run only the permission_gate unit tests (no app required)",
    )
    parser.add_argument(
        "--repo", default="https://github.com/DanyloTs/blender-microservice.git",
        help="GitHub repo URL to use when creating a new project",
    )
    parser.add_argument(
        "--project-id",
        help="Reuse an existing project UUID instead of creating a new one",
    )
    parser.add_argument(
        "--action", choices=["approve", "deny"], default="approve",
        help="How to resolve pending approvals (default: approve)",
    )
    parser.add_argument(
        "--user-id", type=int,
        help="Override user_id (default: E2E_USER_ID env var or 7)",
    )
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Clyde AI — Approval Flow Test")
    print("=" * 60)

    unit_ok = run_unit_tests()

    if args.unit_only:
        sys.exit(0 if unit_ok else 1)

    integration_ok = asyncio.run(run_integration_tests(args))

    print()
    print("─" * 60)
    print(f"  Unit tests      : {'PASS' if unit_ok else 'FAIL'}")
    print(f"  Integration     : {'PASS' if integration_ok else 'FAIL'}")
    print("─" * 60)
    print()

    sys.exit(0 if (unit_ok and integration_ok) else 1)


if __name__ == "__main__":
    main()
