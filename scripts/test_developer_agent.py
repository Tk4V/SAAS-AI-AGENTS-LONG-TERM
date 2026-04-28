#!/usr/bin/env python3
"""Direct developer agent test — bypasses the HTTP API and runs DeveloperAgent in-process.

Useful for rapid iteration: no server needed, logs stream directly to stdout.

Usage:
    # Code task — make changes to a repo
    python scripts/test_developer_agent.py \
        --repo https://github.com/owner/repo \
        --prompt "Add a health check endpoint"

    # Code task + Jira — analyze repo and create tickets (requires Jira OAuth seeded in DB)
    python scripts/test_developer_agent.py \
        --repo https://github.com/owner/repo \
        --prompt "Analyze this repo and create Jira tickets for the top 3 improvements"

    # Different branch
    python scripts/test_developer_agent.py \
        --repo https://github.com/owner/repo \
        --branch feature/my-branch \
        --prompt "Refactor the auth module"

Prerequisites:
    1. .env with DB creds, FERNET_KEY, and a seeded GitHub OAuth token for user_id=1
    2. (Optional) Jira OAuth token seeded for user_id=1 if using --with-jira
    3. python scripts/e2e_setup.py --github-token ghp_... --repo ... (to seed)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime

from dotenv import load_dotenv

load_dotenv()

# Ensure project root is on sys.path when running from repo root
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class Logger:
    @staticmethod
    def info(message: str) -> None:
        timestamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

    @staticmethod
    def section(title: str) -> None:
        print()
        print("=" * 60)
        print(f"  {title}")
        print("=" * 60)
        print()


async def run_agent(args: argparse.Namespace) -> None:
    from src.db.session import db
    from src.agents.dev_team.developer_agent import DeveloperAgent

    await db.init()

    state: dict = {
        "task_id": f"test-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}",
        "user_id": args.user_id,
        "project_id": "test-project",
        "description": args.prompt,
        "repos": [
            {
                "url": args.repo,
                "default_branch": args.branch,
            }
        ],
        "attempt": 0,
        "events": [],
    }

    Logger.section("Developer Agent — Direct Test")
    Logger.info(f"User ID  : {args.user_id}")
    Logger.info(f"Repo     : {args.repo} @ {args.branch}")
    Logger.info(f"Jira     : {'enabled' if args.with_jira else 'used if credential exists in DB'}")
    Logger.info(f"Prompt   : {args.prompt[:120]}")

    print()
    Logger.info("Starting DeveloperAgent...")
    print()

    agent = DeveloperAgent()
    try:
        result = await agent(state)
    except Exception as exc:
        Logger.info(f"Agent failed: {exc}")
        raise

    Logger.section("Result")

    summary = result.get("context", {}).get("summary", "")
    if summary:
        Logger.info("Session summary:")
        print()
        print(summary)
        print()

    diffs = result.get("diffs", {})
    if diffs:
        Logger.info("File changes:")
        for repo_name, changes in diffs.items():
            for change in changes:
                print(f"  [{change['action']:6s}] {repo_name}/{change['path']}")
        print()
    else:
        Logger.info("No file changes recorded.")

    events = result.get("events", [])
    if events:
        Logger.info(f"Events: {len(events)}")
        for event in events:
            print(f"  {event['name']} — {json.dumps(event.get('payload', {}))}")
        print()

    if args.verbose:
        print()
        print(json.dumps(result, indent=2, default=str))

    await db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run DeveloperAgent directly without the HTTP API"
    )
    parser.add_argument("--repo", required=True, help="GitHub repo URL")
    parser.add_argument("--branch", default="main", help="Branch to clone (default: main)")
    parser.add_argument("--prompt", required=True, help="Task description for the agent")
    parser.add_argument(
        "--user-id",
        type=int,
        default=int(os.getenv("E2E_USER_ID", "1")),
        help="User ID whose OAuth credentials to use (default: E2E_USER_ID env or 1)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print full result JSON")
    args = parser.parse_args()

    asyncio.run(run_agent(args))


if __name__ == "__main__":
    main()
