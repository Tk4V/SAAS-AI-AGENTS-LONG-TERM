#!/usr/bin/env python3
"""Validate team_agent_configs migration + admin API endpoints.

Tests two layers:
  1. DB — queries team_agent_configs + team_agent_system_tools directly via the repo
  2. API — hits the running app's /admin/team-agents endpoints

Usage (after `alembic upgrade head`):
    python scripts/test_team_agent_configs.py [--token <admin-jwt>]

The --token is required for the API layer. Skip API tests by omitting it.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "\033[92m✓\033[0m"
FAIL = "\033[91m✗\033[0m"
SKIP = "\033[93m-\033[0m"


def check(label: str, condition: bool, detail: str = "") -> bool:
    icon = PASS if condition else FAIL
    suffix = f"  ({detail})" if detail else ""
    print(f"  {icon} {label}{suffix}")
    return condition


async def test_db() -> bool:
    # Import only what doesn't trigger the circular chain
    from src.db.session import db
    from src.db.queries.agent_config_query import TeamAgentConfigRepository

    # db is initialised at import time; no explicit init call needed
    all_ok = True

    print("── DB layer ─────────────────────────────────────────────")
    async with db.session_scope() as session:
        repo = TeamAgentConfigRepository(session)

        configs = await repo.list_all()
        names = {c.name for c in configs}
        all_ok &= check("list_all returns 2 rows", len(configs) == 2, f"got {len(configs)}")
        all_ok &= check("'orchestrator' present", "orchestrator" in names)
        all_ok &= check("'publisher' present", "publisher" in names)

        orch = await repo.get("orchestrator")
        all_ok &= check("get('orchestrator') not None", orch is not None)
        if orch:
            all_ok &= check("orchestrator.model set", bool(orch.model), orch.model)
            all_ok &= check("orchestrator.system_prompt non-empty", len(orch.system_prompt) > 100)
            all_ok &= check("orchestrator.prompt_template is None", orch.prompt_template is None)
            patterns = [st.system_tool.pattern for st in orch.system_tools if st.is_active]
            all_ok &= check(
                f"orchestrator has >= 8 system_tools",
                len(patterns) >= 8,
                f"got {len(patterns)}: {patterns}",
            )
            all_ok &= check("mcp__memory__* in tools", "mcp__memory__*" in patterns)
            all_ok &= check("Read in tools", "Read" in patterns)
            all_ok &= check("Agent in tools", "Agent" in patterns)

        pub = await repo.get("publisher")
        all_ok &= check("get('publisher') not None", pub is not None)
        if pub:
            all_ok &= check("publisher.model set", bool(pub.model), pub.model)
            all_ok &= check("publisher.system_prompt non-empty", len(pub.system_prompt) > 50)
            all_ok &= check("publisher.prompt_template set", bool(pub.prompt_template))
            if pub.prompt_template:
                placeholders_ok = all(
                    p in pub.prompt_template
                    for p in ["{description}", "{repo_name}", "{changes_summary}", "{plan_summary}"]
                )
                all_ok &= check("prompt_template has all {placeholders}", placeholders_ok)
            all_ok &= check(
                "publisher has no system_tools (direct API call)",
                len(pub.system_tools) == 0,
            )

    await db.dispose()
    return all_ok


async def test_api(base_url: str, token: str) -> bool:
    all_ok = True
    headers = {"Authorization": f"Bearer {token}"}

    print()
    print("── API layer ────────────────────────────────────────────")
    async with httpx.AsyncClient(base_url=base_url, headers=headers, timeout=10) as client:
        # List
        r = await client.get("/admin/team-agents")
        all_ok &= check("GET /admin/team-agents → 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            items = r.json()
            names = {i["name"] for i in items}
            all_ok &= check("response contains orchestrator + publisher", names >= {"orchestrator", "publisher"})
            orch_item = next((i for i in items if i["name"] == "orchestrator"), None)
            if orch_item:
                all_ok &= check(
                    "orchestrator has system_tools in response",
                    len(orch_item.get("system_tools", [])) >= 8,
                    f"got {len(orch_item.get('system_tools', []))}",
                )

        # Get one
        r = await client.get("/admin/team-agents/orchestrator")
        all_ok &= check("GET /admin/team-agents/orchestrator → 200", r.status_code == 200, str(r.status_code))

        r = await client.get("/admin/team-agents/publisher")
        all_ok &= check("GET /admin/team-agents/publisher → 200", r.status_code == 200, str(r.status_code))

        r = await client.get("/admin/team-agents/nonexistent")
        all_ok &= check("GET /admin/team-agents/nonexistent → 404", r.status_code == 404, str(r.status_code))

        # Patch (round-trip display_name, then restore)
        r = await client.patch(
            "/admin/team-agents/publisher",
            json={"display_name": "Publisher (test)"},
        )
        all_ok &= check("PATCH /admin/team-agents/publisher → 200", r.status_code == 200, str(r.status_code))
        if r.status_code == 200:
            all_ok &= check("display_name updated", r.json()["display_name"] == "Publisher (test)")
            # Restore
            await client.patch("/admin/team-agents/publisher", json={"display_name": "Publisher"})

    return all_ok


async def main(args: argparse.Namespace) -> None:
    print()
    print("=" * 60)
    print("  Team Agent Config — integration test")
    print("=" * 60)
    print()

    db_ok = await test_db()

    api_ok = True
    if args.token:
        api_prefix = os.getenv("API_PREFIX", "/api/v1")
        base_url = f"http://localhost:8000{api_prefix}"
        api_ok = await test_api(base_url, args.token)
    else:
        print()
        print(f"  {SKIP} API tests skipped (pass --token <admin-jwt> to enable)")

    print()
    print("=" * 60)
    all_ok = db_ok and api_ok
    if all_ok:
        print(f"  {PASS} All checks passed")
    else:
        print(f"  {FAIL} Some checks failed")
    print("=" * 60)
    print()
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test team_agent_configs DB + API")
    parser.add_argument("--token", default="", help="Admin JWT for API layer tests")
    asyncio.run(main(parser.parse_args()))
