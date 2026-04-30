#!/usr/bin/env python3
"""Provider-specific end-to-end test: pick a provider, run its OAuth + task flow.

Usage:
    # GitHub only — code pipeline (interactive repo selection)
    python scripts/e2e_provider_test.py --provider github \
        --prompt "Fix the authentication bug"

    # GitHub + Jira — analyze repo code and create Jira tickets from it
    python scripts/e2e_provider_test.py --provider github+jira

    # GitHub + Jira — custom prompt
    python scripts/e2e_provider_test.py --provider github+jira \
        --prompt "Analyze this repo and create Jira tickets for the top 3 bugs found"

Prerequisites:
    1. App running: docker compose -f docker-compose-dev.yaml up --build
    2. OAuth apps registered for the chosen providers with the correct callback URLs
    3. .env filled with CLIENT_ID / CLIENT_SECRET for each provider and FERNET_KEY
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

SUPPORTED_PROVIDERS = ("github", "github+jira")

# Default prompts used when --prompt is not supplied
PROVIDER_DEFAULT_PROMPTS: dict[str, str] = {
    "github": "Analyze this repository and suggest improvements",
    "github+jira": (
        "Analyze this repository's code and create Jira tickets for the top 3 "
        "improvements or bugs you find. For each ticket include: a clear summary, "
        "a description of the issue with relevant file paths, and suggested fix. "
        "Don't assign tickets to anyone."
    ),
}


# ---------------------------------------------------------------------------
# Config / JWT
# ---------------------------------------------------------------------------


class E2EConfig:
    """Reads environment variables for the E2E test."""

    def __init__(self, *, user_id: int | None = None) -> None:
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-shared-with-django")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "")
        self.app_port = int(os.getenv("PORT", "8000"))
        self.api_prefix = os.getenv("API_PREFIX", "/api/v1")
        self.user_id = user_id or int(os.getenv("E2E_USER_ID", "1"))

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.app_port}{self.api_prefix}"


class JWTGenerator:
    @staticmethod
    def create(config: E2EConfig) -> str:
        now = datetime.now(UTC)
        payload: dict = {
            "user_id": config.user_id,
            "username": "e2e_tester",
            "email": "e2e@test.local",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=7)).timestamp()),
        }
        if config.jwt_audience:
            payload["aud"] = config.jwt_audience
        return pyjwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class Logger:
    @staticmethod
    def info(message: str) -> None:
        timestamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{timestamp}] {message}")

    @staticmethod
    def progress(elapsed_seconds: int, status: str, attempt: int) -> None:
        sys.stdout.write(
            f"\r  [{elapsed_seconds}s] status={status} attempt={attempt}    "
        )
        sys.stdout.flush()


class HealthChecker:
    @staticmethod
    async def check(http: httpx.AsyncClient, base_url: str) -> None:
        Logger.info("Checking app health...")
        response = await http.get(f"{base_url}/health")
        if response.status_code != 200:
            Logger.info(f"App not responding: {response.status_code}")
            sys.exit(1)
        data = response.json()
        Logger.info(f"App is up: {data['service']} v{data['version']}")


# ---------------------------------------------------------------------------
# OAuth
# ---------------------------------------------------------------------------


class OAuthFlow:
    """Generic OAuth connect flow — works for any provider slug."""

    @staticmethod
    async def ensure_connected(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        provider: str,
    ) -> None:
        """Skip OAuth if already connected; otherwise open the browser."""
        response = await http.get(
            f"{base_url}/credentials", params={"kind": "oauth"}, headers=headers
        )
        if response.status_code == 200:
            items = response.json().get("items", [])
            if any(
                item.get("metadata", {}).get("provider") == provider for item in items
            ):
                Logger.info(f"{provider.title()} already connected, skipping OAuth")
                return

        Logger.info(f"Starting {provider.title()} OAuth flow...")
        response = await http.get(
            f"{base_url}/credentials/oauth/{provider}/authorize", headers=headers
        )
        if response.status_code != 200:
            Logger.info(f"OAuth start failed: {response.status_code} {response.text}")
            sys.exit(1)

        authorization_url = response.json()["authorization_url"]
        Logger.info("Opening browser for authorization...")
        print(f"\n  If the browser does not open, visit manually:\n  {authorization_url}\n")
        webbrowser.open(authorization_url)
        input("  Press ENTER after you approved the authorization... ")

        Logger.info("Waiting for OAuth callback...")
        if not await OAuthFlow._wait_for_integration(http, base_url, headers, provider):
            Logger.info(f"{provider.title()} integration not found. Check app logs.")
            sys.exit(1)
        Logger.info(f"{provider.title()} connected!")

    @staticmethod
    async def _wait_for_integration(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        provider: str,
    ) -> bool:
        for _ in range(60):
            response = await http.get(
                f"{base_url}/credentials", params={"kind": "oauth"}, headers=headers
            )
            if response.status_code == 200:
                items = response.json().get("items", [])
                if any(
                    item.get("metadata", {}).get("provider") == provider
                    for item in items
                ):
                    return True
            await asyncio.sleep(1)
        return False


# ---------------------------------------------------------------------------
# GitHub repo selection
# ---------------------------------------------------------------------------


class RepoSelector:
    """Manual repository entry. The list-repos API was retired; the UI/script
    now takes the repo URL directly from the user and validates it on attach."""

    @staticmethod
    async def pick(
        http: httpx.AsyncClient, base_url: str, headers: dict[str, str]
    ) -> dict:
        url = input("  Paste a GitHub repo URL (e.g. https://github.com/owner/name): ").strip()
        if not url:
            Logger.info("No repo URL provided.")
            sys.exit(1)
        full_name = url.rstrip("/").removeprefix("https://github.com/")
        branch = input("  Branch [main]: ").strip() or "main"
        Logger.info(f"Selected: {full_name} (branch: {branch})")
        return {"full_name": full_name, "url": url, "default_branch": branch}


# ---------------------------------------------------------------------------
# Project creation
# ---------------------------------------------------------------------------


class ProjectCreator:
    @staticmethod
    async def create(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        *,
        repo: dict | None = None,
    ) -> str:
        """Create a project, optionally with a GitHub repo attached."""
        project_name = f"e2e-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"
        repos_payload: list[dict] = []

        if repo:
            repos_payload = [
                {"url": repo["url"], "default_branch": repo["default_branch"]}
            ]
            Logger.info(
                f"Creating project '{project_name}' with {repo['full_name']}..."
            )
        else:
            Logger.info(f"Creating project '{project_name}' (no repo attached)...")

        response = await http.post(
            f"{base_url}/projects",
            headers=headers,
            json={
                "name": project_name,
                "description": "E2E provider test project",
                "repos": repos_payload,
            },
        )
        if response.status_code != 201:
            Logger.info(
                f"Project creation failed: {response.status_code} {response.text}"
            )
            sys.exit(1)

        project_id = response.json()["id"]
        Logger.info(f"Project created: {project_id}")
        return project_id


# ---------------------------------------------------------------------------
# Task runner
# ---------------------------------------------------------------------------


class TaskRunner:
    @staticmethod
    async def run(
        http: httpx.AsyncClient,
        base_url: str,
        headers: dict[str, str],
        project_id: str,
        prompt: str,
        verbose: bool = False,
    ) -> None:
        Logger.info(f'Creating task: "{prompt[:100]}"')
        response = await http.post(
            f"{base_url}/tasks",
            headers=headers,
            json={"project_id": project_id, "description": prompt},
        )
        if response.status_code != 201:
            Logger.info(
                f"Task creation failed: {response.status_code} {response.text}"
            )
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

                if (
                    current_status in ("failed", "needs_human")
                    and retries_remaining > 0
                ):
                    retries_remaining -= 1
                    Logger.info(f"Retrying... ({retries_remaining} retries left)")
                    retry_resp = await http.post(
                        f"{base_url}/tasks/{task_id}/retry", headers=headers
                    )
                    if retry_resp.status_code == 200:
                        last_status = "running"
                        continue
                    Logger.info(f"Retry failed: {retry_resp.status_code}")
                return

        print()
        Logger.info(f"Timed out after {MAX_POLL_ATTEMPTS * POLL_INTERVAL_SECONDS}s")

    @staticmethod
    def _print_results(task_data: dict, verbose: bool) -> None:
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


# ---------------------------------------------------------------------------
# Per-provider flows
# ---------------------------------------------------------------------------


async def _github_flow(
    http: httpx.AsyncClient,
    config: E2EConfig,
    headers: dict[str, str],
    prompt: str,
    verbose: bool,
) -> None:
    """GitHub flow: OAuth → pick repo → project → code-pipeline task."""
    base_url = config.base_url
    await OAuthFlow.ensure_connected(http, base_url, headers, provider="github")
    repo = await RepoSelector.pick(http, base_url, headers)
    project_id = await ProjectCreator.create(http, base_url, headers, repo=repo)
    await TaskRunner.run(
        http, base_url, headers,
        project_id=project_id,
        prompt=prompt,
        verbose=verbose,
    )


async def _github_jira_flow(
    http: httpx.AsyncClient,
    config: E2EConfig,
    headers: dict[str, str],
    prompt: str,
    verbose: bool,
) -> None:
    """GitHub + Jira flow: OAuth both → pick repo → project → analyze code and create tickets."""
    base_url = config.base_url
    await OAuthFlow.ensure_connected(http, base_url, headers, provider="github")
    await OAuthFlow.ensure_connected(http, base_url, headers, provider="jira")
    repo = await RepoSelector.pick(http, base_url, headers)
    project_id = await ProjectCreator.create(http, base_url, headers, repo=repo)
    await TaskRunner.run(
        http, base_url, headers,
        project_id=project_id,
        prompt=prompt,
        verbose=verbose,
    )


_PROVIDER_FLOWS = {
    "github": _github_flow,
    "github+jira": _github_jira_flow,
}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def run_e2e(args: argparse.Namespace) -> None:
    config = E2EConfig(user_id=args.user_id)
    token = JWTGenerator.create(config)
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=30.0) as http:
        await HealthChecker.check(http, config.base_url)
        await _PROVIDER_FLOWS[args.provider](
            http, config, headers, args.prompt, args.verbose
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clyde AI — provider E2E test (OAuth → task → poll)"
    )
    parser.add_argument(
        "--provider",
        required=True,
        choices=SUPPORTED_PROVIDERS,
        metavar="PROVIDER",
        help=f"Provider to test. Choices: {', '.join(SUPPORTED_PROVIDERS)}",
    )
    parser.add_argument(
        "--prompt",
        default=None,
        help="Task description (omit to use the provider's built-in default prompt)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full task JSON on completion",
    )
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="User ID to authenticate as (default: E2E_USER_ID env var or 1)",
    )
    args = parser.parse_args()

    if args.prompt is None:
        args.prompt = PROVIDER_DEFAULT_PROMPTS[args.provider]

    print()
    print("=" * 60)
    print(f"  Clyde AI — {args.provider.upper()} E2E Test")
    print("=" * 60)
    print()
    Logger.info(f"Provider : {args.provider}")
    Logger.info(f"User ID  : {args.user_id or int(os.getenv('E2E_USER_ID', '1'))}")
    Logger.info(f"Prompt   : {args.prompt[:120]}")
    print()

    asyncio.run(run_e2e(args))


if __name__ == "__main__":
    main()
