#!/usr/bin/env python3
"""Full end-to-end test: OAuth -> pick repo -> project -> task -> pipeline -> PR.

Opens a browser for real GitHub OAuth, lets you pick a repo from your account,
then automates everything from project creation to PR.

Prerequisites:
    1. App running: docker compose -f docker-compose-dev.yaml up --build
    2. GitHub OAuth App registered with callback:
       http://localhost:8000/api/v1/auth/oauth/github/callback
    3. .env filled: GITHUB_OAUTH_CLIENT_ID, GITHUB_OAUTH_CLIENT_SECRET, FERNET_KEY

Usage:
    python scripts/e2e_test.py --prompt "Fix the authentication bug"
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

POLL_INTERVAL_SECONDS = 5
MAX_POLL_ATTEMPTS = 180
AUTO_RETRY_LIMIT = 2
TERMINAL_STATUSES = {"completed", "awaiting_ci", "needs_human", "failed"}


class E2EConfig:
    """Reads environment variables for the E2E test."""

    def __init__(self) -> None:
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-shared-with-django")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "")
        self.app_port = int(os.getenv("PORT", "8000"))
        self.api_prefix = os.getenv("API_PREFIX", "/api/v1")
        self.user_id = 1

    @property
    def base_url(self) -> str:
        """Build the API base URL."""
        return f"http://localhost:{self.app_port}{self.api_prefix}"

    @property
    def websocket_base(self) -> str:
        """Build the WebSocket base URL."""
        return f"ws://localhost:{self.app_port}{self.api_prefix}"


class Logger:
    """Simple timestamped logger for E2E output."""

    @staticmethod
    def info(message: str) -> None:
        """Print a timestamped log message."""
        timestamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

    @staticmethod
    def progress(elapsed_seconds: int, status: str, attempt: int) -> None:
        """Print an in-place progress update."""
        sys.stdout.write(
            f"\r  [{elapsed_seconds}s] status={status} attempt={attempt}    "
        )
        sys.stdout.flush()


class JWTGenerator:
    """Creates JWT tokens for the E2E test user."""

    @staticmethod
    def create(config: E2EConfig) -> str:
        """Create a JWT token valid for 7 days."""
        now = datetime.now(UTC)
        payload = {
            "user_id": config.user_id,
            "username": "e2e_tester",
            "email": "e2e@test.local",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=7)).timestamp()),
        }
        if config.jwt_audience:
            payload["aud"] = config.jwt_audience
        return pyjwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


class HealthChecker:
    """Verifies the application is running."""

    @staticmethod
    async def check(http: httpx.AsyncClient, base_url: str) -> None:
        """Check app health and exit if not responding."""
        Logger.info("Checking app health...")
        response = await http.get(f"{base_url}/health")
        if response.status_code != 200:
            Logger.info(f"App not responding: {response.status_code}")
            sys.exit(1)
        data = response.json()
        Logger.info(f"App is up: {data['service']} v{data['version']}")


class OAuthFlow:
    """Handles GitHub OAuth connection."""

    @staticmethod
    async def ensure_connected(
        http: httpx.AsyncClient, base_url: str, headers: dict[str, str]
    ) -> None:
        """Check if GitHub is connected; if not, run the OAuth flow."""
        response = await http.get(f"{base_url}/auth/integrations", headers=headers)
        if response.status_code == 200:
            items = response.json().get("items", [])
            if any(item.get("provider") == "github" for item in items):
                Logger.info("GitHub already connected, skipping OAuth")
                return

        Logger.info("Starting GitHub OAuth flow...")
        response = await http.get(f"{base_url}/auth/oauth/github/start", headers=headers)
        if response.status_code != 200:
            Logger.info(f"OAuth start failed: {response.status_code} {response.text}")
            sys.exit(1)

        authorization_url = response.json()["authorization_url"]
        Logger.info("Opening browser for GitHub authorization...")
        print(f"\n  If the browser does not open, visit manually:\n  {authorization_url}\n")
        webbrowser.open(authorization_url)
        input("  Press ENTER after you approved on GitHub... ")

        Logger.info("Waiting for OAuth callback...")
        if not await OAuthFlow._wait_for_integration(http, base_url, headers):
            Logger.info("GitHub integration not found. Check app logs.")
            sys.exit(1)
        Logger.info("GitHub connected!")

    @staticmethod
    async def _wait_for_integration(
        http: httpx.AsyncClient, base_url: str, headers: dict[str, str]
    ) -> bool:
        """Poll integrations endpoint until GitHub appears or timeout."""
        for _ in range(60):
            response = await http.get(f"{base_url}/auth/integrations", headers=headers)
            if response.status_code == 200:
                items = response.json().get("items", [])
                if any(item.get("provider") == "github" for item in items):
                    return True
            await asyncio.sleep(1)
        return False


class RepoSelector:
    """Interactive repository selection from user's GitHub account."""

    @staticmethod
    async def pick(
        http: httpx.AsyncClient, base_url: str, headers: dict[str, str]
    ) -> dict:
        """Fetch repos and let the user choose interactively."""
        Logger.info("Fetching your GitHub repositories...")
        response = await http.get(
            f"{base_url}/auth/integrations/github/repos", headers=headers, timeout=30.0
        )
        if response.status_code != 200:
            Logger.info(f"Failed to fetch repos: {response.status_code} {response.text}")
            sys.exit(1)

        repos = response.json()["items"]
        if not repos:
            Logger.info("No repositories found on your GitHub account.")
            sys.exit(1)

        print(f"\n  Found {len(repos)} repositories. Pick one:\n")
        for index, repo in enumerate(repos, 1):
            private_label = " (private)" if repo["private"] else ""
            description = f" — {repo['description'][:60]}" if repo["description"] else ""
            print(f"  {index:3d}. {repo['full_name']}{private_label}{description}")

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
            clean_branch = branch_input.encode("ascii", errors="ignore").decode("ascii").strip()
            if clean_branch:
                selected["default_branch"] = clean_branch

        Logger.info(f"Selected: {selected['full_name']} (branch: {selected['default_branch']})")
        return selected


class ProjectCreator:
    """Creates a project with an attached repository."""

    @staticmethod
    async def create(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        repo: dict,
    ) -> str:
        """Create a project with the selected repo. Returns the project ID."""
        project_name = f"e2e-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        Logger.info(f"Creating project '{project_name}' with {repo['full_name']}...")

        response = await http.post(
            f"{base_url}/projects",
            headers=headers,
            json={
                "name": project_name,
                "description": f"E2E test on {repo['full_name']}",
                "repos": [{"url": repo["url"], "default_branch": repo["default_branch"]}],
            },
        )
        if response.status_code != 201:
            Logger.info(f"Project creation failed: {response.status_code} {response.text}")
            sys.exit(1)

        project_id = response.json()["id"]
        Logger.info(f"Project created: {project_id}")
        return project_id


class TaskRunner:
    """Creates a task and polls until the pipeline finishes."""

    @staticmethod
    async def run(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        project_id: str,
        prompt: str,
        verbose: bool = False,
    ) -> None:
        """Create a task, poll until terminal status, auto-retry on failure."""
        Logger.info(f'Creating task: "{prompt[:80]}"')
        response = await http.post(
            f"{base_url}/tasks",
            headers=headers,
            json={"project_id": project_id, "description": prompt},
        )
        if response.status_code != 201:
            Logger.info(f"Task creation failed: {response.status_code} {response.text}")
            sys.exit(1)

        task_id = response.json()["id"]
        Logger.info(f"Task created: {task_id}")
        print()
        Logger.info("Pipeline is running. Polling for results...")
        print()

        last_status = "running"
        retries_remaining = AUTO_RETRY_LIMIT

        for poll_number in range(1, MAX_POLL_ATTEMPTS + 1):
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            response = await http.get(f"{base_url}/tasks/{task_id}", headers=headers)
            if response.status_code != 200:
                continue

            task_data = response.json()
            current_status = task_data["status"]

            if current_status != last_status:
                Logger.info(f"  Status: {last_status} -> {current_status}")
                last_status = current_status
            else:
                elapsed = poll_number * POLL_INTERVAL_SECONDS
                Logger.progress(elapsed, current_status, task_data.get("attempt", 0))

            if current_status in TERMINAL_STATUSES:
                print("\n")
                Logger.info(f"Pipeline finished: {current_status}")

                TaskRunner._print_results(task_data, verbose)

                if current_status in ("failed", "needs_human") and retries_remaining > 0:
                    retries_remaining -= 1
                    Logger.info(f"Retrying... ({retries_remaining} retries left)")
                    retry_response = await http.post(
                        f"{base_url}/tasks/{task_id}/retry", headers=headers
                    )
                    if retry_response.status_code == 200:
                        last_status = "running"
                        continue
                    Logger.info(f"Retry failed: {retry_response.status_code}")
                return

        print()
        Logger.info(f"Timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s")

    @staticmethod
    def _print_results(task_data: dict, verbose: bool) -> None:
        """Print PR URLs, errors, or full JSON depending on the result."""
        if task_data.get("pr_urls"):
            print()
            Logger.info("Pull Requests created:")
            for repo_name, url in task_data["pr_urls"].items():
                Logger.info(f"  {repo_name}: {url}")

        if task_data.get("error_message"):
            print()
            Logger.info(f"Error: {task_data['error_message'][:500]}")

        if verbose:
            print()
            print(json.dumps(task_data, indent=2, default=str))


async def run_e2e(args: argparse.Namespace) -> None:
    """Execute the full E2E flow."""
    config = E2EConfig()
    token = JWTGenerator.create(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        base_url = config.base_url

        await HealthChecker.check(http, base_url)
        await OAuthFlow.ensure_connected(http, base_url, headers)

        repo = await RepoSelector.pick(http, base_url, headers)
        project_id = await ProjectCreator.create(http, base_url, headers, repo)

        await TaskRunner.run(
            http, base_url, headers,
            project_id=project_id,
            prompt=args.prompt,
            verbose=args.verbose,
        )


def main() -> None:
    """Entry point: parse args and run the E2E test."""
    parser = argparse.ArgumentParser(
        description="E2E test: OAuth -> pick repo -> task -> pipeline -> PR"
    )
    parser.add_argument("--prompt", required=True, help="Task description for the agents")
    parser.add_argument("--verbose", action="store_true", help="Print full JSON on completion")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Clyde AI — End-to-End Test")
    print("=" * 60)
    print()

    asyncio.run(run_e2e(args))


if __name__ == "__main__":
    main()
