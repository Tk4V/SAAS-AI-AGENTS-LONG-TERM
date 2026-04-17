#!/usr/bin/env python3
"""Full end-to-end test: OAuth → pick repo → project → task → pipeline → PR.

Opens a browser for real GitHub OAuth, lets you pick a repo from your account,
then automates everything from project creation to PR.

Prerequisites:
  1. App running: docker compose -f docker-compose-dev.yaml up --build
  2. GitHub OAuth App registered with callback:
     http://localhost:8000/api/v1/auth/oauth/github/callback
  3. .env filled: GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, FERNET_KEY

Usage:
  .venv/bin/python scripts/e2e_test.py --prompt "Fix the authentication bug"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import webbrowser
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
from dotenv import load_dotenv

load_dotenv()

JWT_SECRET = os.getenv("JWT_SECRET", "change-me-shared-with-django")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
JWT_AUDIENCE = os.getenv("JWT_AUDIENCE", "")
APP_PORT = int(os.getenv("PORT", "8000"))
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")

BASE = f"http://localhost:{APP_PORT}{API_PREFIX}"
USER_ID = 1
POLL_INTERVAL = 5
MAX_POLLS = 180


def log(msg: str) -> None:
    ts = datetime.now(UTC).strftime("%H:%M:%S")
    print(f"[{ts}] {msg}")


def make_jwt() -> str:
    now = datetime.now(UTC)
    payload = {
        "user_id": USER_ID,
        "username": "e2e_tester",
        "email": "e2e@test.local",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=7)).timestamp()),
    }
    if JWT_AUDIENCE:
        payload["aud"] = JWT_AUDIENCE
    return pyjwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


async def wait_for_integration(http: httpx.AsyncClient, headers: dict) -> bool:
    for _ in range(60):
        r = await http.get(f"{BASE}/auth/integrations", headers=headers)
        if r.status_code == 200:
            items = r.json().get("items", [])
            if any(i.get("provider") == "github" for i in items):
                return True
        await asyncio.sleep(1)
    return False


async def ensure_github_connected(http: httpx.AsyncClient, headers: dict) -> None:
    """Check if GitHub is connected; if not, run the OAuth flow."""
    r = await http.get(f"{BASE}/auth/integrations", headers=headers)
    if r.status_code == 200:
        items = r.json().get("items", [])
        if any(i.get("provider") == "github" for i in items):
            log("GitHub already connected, skipping OAuth")
            return

    log("Starting GitHub OAuth flow...")
    r = await http.get(f"{BASE}/auth/oauth/github/start", headers=headers)
    if r.status_code != 200:
        log(f"OAuth start failed: {r.status_code} {r.text}")
        sys.exit(1)

    auth_url = r.json()["authorization_url"]
    log("Opening browser for GitHub authorization...")
    print()
    print(f"  If the browser does not open, visit manually:")
    print(f"  {auth_url}")
    print()

    webbrowser.open(auth_url)
    input("  Press ENTER after you approved on GitHub... ")
    print()

    log("Waiting for OAuth callback...")
    if not await wait_for_integration(http, headers):
        log("GitHub integration not found. Check app logs.")
        sys.exit(1)
    log("GitHub connected!")


async def pick_repo(http: httpx.AsyncClient, headers: dict) -> dict:
    """Fetch the user's repos and let them choose interactively."""
    log("Fetching your GitHub repositories...")
    r = await http.get(
        f"{BASE}/auth/integrations/github/repos", headers=headers, timeout=30.0
    )
    if r.status_code != 200:
        log(f"Failed to fetch repos: {r.status_code} {r.text}")
        sys.exit(1)

    repos = r.json()["items"]
    if not repos:
        log("No repositories found on your GitHub account.")
        sys.exit(1)

    print()
    print(f"  Found {len(repos)} repositories. Pick one:")
    print()
    for i, repo in enumerate(repos, 1):
        private = " (private)" if repo["private"] else ""
        desc = f" — {repo['description'][:60]}" if repo["description"] else ""
        print(f"  {i:3d}. {repo['full_name']}{private}{desc}")

    print()
    while True:
        choice = input(f"  Enter number (1-{len(repos)}): ").strip()
        if choice.isdigit() and 1 <= int(choice) <= len(repos):
            selected = repos[int(choice) - 1]
            break
        print("  Invalid choice, try again.")

    default_branch = selected["default_branch"]
    branch_input = input(f"  Branch [{default_branch}]: ").strip()
    if branch_input:
        selected["default_branch"] = branch_input

    log(f"Selected: {selected['full_name']} (branch: {selected['default_branch']})")
    return selected


async def run(args: argparse.Namespace) -> None:
    token = make_jwt()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as http:

        # 1. Health
        log("Checking app health...")
        r = await http.get(f"{BASE}/health")
        if r.status_code != 200:
            log(f"App not responding: {r.status_code}")
            sys.exit(1)
        log(f"App is up: {r.json()['service']} v{r.json()['version']}")

        # 2. GitHub OAuth
        await ensure_github_connected(http, headers)

        # 3. Pick repo
        repo = await pick_repo(http, headers)

        # 4. Create project
        project_name = f"e2e-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        log(f"Creating project '{project_name}' with {repo['full_name']}...")
        r = await http.post(
            f"{BASE}/projects",
            headers=headers,
            json={
                "name": project_name,
                "description": f"E2E test on {repo['full_name']}",
                "repos": [{
                    "url": repo["url"],
                    "default_branch": repo["default_branch"],
                }],
            },
        )
        if r.status_code != 201:
            log(f"Project creation failed: {r.status_code} {r.text}")
            sys.exit(1)
        project = r.json()
        project_id = project["id"]
        log(f"Project created: {project_id}")

        # 5. Create task
        log(f"Creating task: \"{args.prompt[:80]}\"")
        r = await http.post(
            f"{BASE}/tasks",
            headers=headers,
            json={"project_id": project_id, "description": args.prompt},
        )
        if r.status_code != 201:
            log(f"Task creation failed: {r.status_code} {r.text}")
            sys.exit(1)
        task = r.json()
        task_id = task["id"]
        log(f"Task created: {task_id}")
        print()
        log("Pipeline is running. Polling for results...")
        print()

        # 6. Poll
        terminal = {"completed", "awaiting_ci", "needs_human", "failed"}
        last_status = "running"
        retries_left = 2
        for poll in range(1, MAX_POLLS + 1):
            await asyncio.sleep(POLL_INTERVAL)
            r = await http.get(f"{BASE}/tasks/{task_id}", headers=headers)
            if r.status_code != 200:
                continue

            data = r.json()
            status_val = data["status"]

            if status_val != last_status:
                log(f"  Status: {last_status} -> {status_val}")
                last_status = status_val
            else:
                elapsed = poll * POLL_INTERVAL
                sys.stdout.write(f"\r  [{elapsed}s] status={status_val} attempt={data.get('attempt', 0)}    ")
                sys.stdout.flush()

            if status_val in terminal:
                print()
                print()
                log(f"Pipeline finished: {status_val}")

                if data.get("pr_urls"):
                    print()
                    log("Pull Requests created:")
                    for repo_name, url in data["pr_urls"].items():
                        log(f"  {repo_name}: {url}")
                    return

                if data.get("error_message"):
                    print()
                    log(f"Error: {data['error_message'][:500]}")

                if args.verbose:
                    print()
                    print(json.dumps(data, indent=2, default=str))

                # Auto-retry on failure
                if status_val in ("failed", "needs_human") and retries_left > 0:
                    retries_left -= 1
                    log(f"Retrying... ({retries_left} retries left)")
                    r = await http.post(
                        f"{BASE}/tasks/{task_id}/retry", headers=headers
                    )
                    if r.status_code == 200:
                        last_status = "running"
                        continue
                    else:
                        log(f"Retry failed: {r.status_code} {r.text[:200]}")
                return

        print()
        log(f"Timed out after {MAX_POLLS * POLL_INTERVAL}s")


def main() -> None:
    parser = argparse.ArgumentParser(description="E2E: OAuth -> pick repo -> task -> PR")
    parser.add_argument("--prompt", required=True, help="Task description for the agents")
    parser.add_argument("--verbose", action="store_true", help="Print full JSON on completion")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Clyde AI — End-to-End Test")
    print("=" * 60)
    print()

    asyncio.run(run(args))


if __name__ == "__main__":
    main()
