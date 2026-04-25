#!/usr/bin/env python3
"""Seed test data for a full end-to-end pipeline run.

Inserts a GitHub OAuth token, creates a project with a repo attached,
and prints ready-to-paste curl commands.

Usage:
    python scripts/e2e_setup.py \
        --github-token ghp_YOUR_TOKEN \
        --repo https://github.com/owner/repo \
        --prompt "Fix the authentication bug"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv()


class E2EConfig:
    """Reads environment variables for the E2E setup script."""

    def __init__(self) -> None:
        self.db_host = os.getenv("DB_HOST", "localhost")
        self.db_port = int(os.getenv("DB_PORT", "5432"))
        self.db_name = os.getenv("DB_NAME", "clyde")
        self.db_user = os.getenv("DB_USER", "clyde")
        self.db_password = os.getenv("DB_PASSWORD", "clyde")
        self.db_ssl = os.getenv("DB_SSL", "disable")
        self.jwt_secret = os.getenv("JWT_SECRET", "change-me-shared-with-django")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_audience = os.getenv("JWT_AUDIENCE", "")
        self.fernet_key = os.getenv("FERNET_KEY", "")
        self.api_prefix = os.getenv("API_PREFIX", "/api/v1")
        self.app_port = int(os.getenv("PORT", "8000"))
        self.user_id = 1

    @property
    def base_url(self) -> str:
        """Build the API base URL."""
        return f"http://localhost:{self.app_port}{self.api_prefix}"


class JWTGenerator:
    """Generates JWT tokens compatible with Django simplejwt."""

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
            "type": "access",
        }
        if config.jwt_audience:
            payload["aud"] = config.jwt_audience
        return jwt.encode(payload, config.jwt_secret, algorithm=config.jwt_algorithm)


class TokenEncryptor:
    """Encrypts tokens using Fernet symmetric encryption."""

    @staticmethod
    def encrypt(plaintext: str, fernet_key: str) -> str:
        """Encrypt a plaintext string and return the ciphertext."""
        fernet = Fernet(fernet_key.encode())
        return fernet.encrypt(plaintext.encode()).decode()


class DatabaseSeeder:
    """Seeds the database with test data for E2E runs."""

    @staticmethod
    async def seed(
        *,
        config: E2EConfig,
        github_token_encrypted: str,
        repo_url: str,
        default_branch: str,
    ) -> tuple[str, str]:
        """Insert OAuth credential, project, and repo. Returns (project_id, repo_id)."""
        import asyncpg

        ssl_mode = config.db_ssl if config.db_ssl != "disable" else None
        connection = await asyncpg.connect(
            host=config.db_host,
            port=config.db_port,
            database=config.db_name,
            user=config.db_user,
            password=config.db_password,
            ssl=ssl_mode,
        )

        try:
            credential_id = str(uuid.uuid4())
            await connection.execute(
                """
                INSERT INTO user_oauth_credentials
                    (id, user_id, provider, token_encrypted, scopes, granted_at, created_at, updated_at)
                VALUES ($1, $2, 'github', $3, 'repo,workflow', now(), now(), now())
                ON CONFLICT (user_id, provider)
                DO UPDATE SET token_encrypted = $3, updated_at = now()
                """,
                uuid.UUID(credential_id),
                config.user_id,
                github_token_encrypted,
            )
            print(f"  GitHub token saved for user_id={config.user_id}")

            project_id = str(uuid.uuid4())
            project_name = f"e2e-test-{datetime.now(UTC).strftime('%H%M%S')}"
            await connection.execute(
                """
                INSERT INTO projects (id, user_id, name, description, created_at, updated_at)
                VALUES ($1, $2, $3, $4, now(), now())
                ON CONFLICT DO NOTHING
                """,
                uuid.UUID(project_id),
                config.user_id,
                project_name,
                "End-to-end test project created by e2e_setup.py",
            )
            print(f"  Project created: {project_id}")

            repo_id = str(uuid.uuid4())
            await connection.execute(
                """
                INSERT INTO project_repos
                    (id, project_id, provider, url, default_branch, created_at, updated_at)
                VALUES ($1, $2, 'github', $3, $4, now(), now())
                """,
                uuid.UUID(repo_id),
                uuid.UUID(project_id),
                repo_url,
                default_branch,
            )
            repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
            print(f"  Repo attached: {repo_name} ({repo_url})")

            return project_id, repo_id

        finally:
            await connection.close()


class CurlCommandPrinter:
    """Prints ready-to-paste curl commands for testing the API."""

    @staticmethod
    def print_commands(
        *,
        config: E2EConfig,
        token: str,
        project_id: str,
        prompt: str,
    ) -> None:
        """Print curl commands for creating and monitoring tasks."""
        base = config.base_url

        task_body = json.dumps({
            "project_id": project_id,
            "description": prompt,
        })

        print("\n# Create a task (starts the pipeline automatically):")
        print(f'curl -s -X POST {base}/tasks \\')
        print(f'  -H "Authorization: Bearer {token}" \\')
        print(f'  -H "Content-Type: application/json" \\')
        print(f"  -d '{task_body}' | python -m json.tool")

        print("\n# Poll task status (replace TASK_ID_HERE):")
        print(f'curl -s {base}/tasks/TASK_ID_HERE \\')
        print(f'  -H "Authorization: Bearer {token}" | python -m json.tool')

        print(f"\n# List all tasks for this project:")
        print(f'curl -s "{base}/tasks?project_id={project_id}" \\')
        print(f'  -H "Authorization: Bearer {token}" | python -m json.tool')

        print(f"\n# Watch pipeline events via WebSocket:")
        print(f"wscat -c 'ws://localhost:{config.app_port}{config.api_prefix}"
              f"/ws/tasks/TASK_ID_HERE?token={token}'")

        print(f"\n# Health check:")
        print(f"curl -s {base}/health | python -m json.tool")


def main() -> None:
    """Run the E2E setup: generate JWT, encrypt token, seed DB, print curl commands."""
    parser = argparse.ArgumentParser(description="Seed test data for E2E pipeline run")
    parser.add_argument("--github-token", required=True, help="GitHub PAT (ghp_...)")
    parser.add_argument("--repo", required=True, help="GitHub repo URL")
    parser.add_argument("--branch", default="main", help="Default branch (default: main)")
    parser.add_argument("--prompt", default="Analyze this repository and suggest improvements")
    args = parser.parse_args()

    config = E2EConfig()

    fernet_key = config.fernet_key
    if not fernet_key:
        fernet_key = Fernet.generate_key().decode()
        print(f"\n  FERNET_KEY not set. Generated: {fernet_key}")
        print(f"  Add to .env: FERNET_KEY={fernet_key}\n")

    print("\n=== Step 1: Generating JWT ===")
    token = JWTGenerator.create(config)
    print(f"  JWT for user_id={config.user_id} (valid 7 days)")

    print("\n=== Step 2: Encrypting GitHub PAT ===")
    encrypted = TokenEncryptor.encrypt(args.github_token, fernet_key)
    print(f"  Encrypted ({len(encrypted)} chars)")

    print("\n=== Step 3: Seeding database ===")
    project_id, _ = asyncio.run(
        DatabaseSeeder.seed(
            config=config,
            github_token_encrypted=encrypted,
            repo_url=args.repo,
            default_branch=args.branch,
        )
    )

    print("\n=== Step 4: Ready! ===")
    CurlCommandPrinter.print_commands(
        config=config,
        token=token,
        project_id=project_id,
        prompt=args.prompt,
    )


if __name__ == "__main__":
    main()
