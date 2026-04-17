"""Seeds test data and prints curl commands for a full end-to-end pipeline run.

Usage (from project root):

  # 1. Make sure the app is running:
  docker compose -f docker-compose-dev.yaml up --build

  # 2. In another terminal, run this script:
  docker run --rm --network host --env-file .env clyde-ai \
    python scripts/e2e_setup.py \
      --github-token ghp_YOUR_TOKEN \
      --repo https://github.com/owner/repo \
      --prompt "Fix the authentication bug"

The script will:
  - Generate a FERNET_KEY if not set
  - Encrypt the GitHub PAT and insert it into user_oauth_credentials
  - Create a project with the given repo attached
  - Print ready-to-paste curl commands for creating a task and polling it
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from cryptography.fernet import Fernet

# Inline settings parsing so the script works without importing the full app
# (which would trigger DB/engine construction).
import os
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = int(os.getenv("DB_PORT", "5432"))
DB_NAME = os.getenv("DB_NAME", "clyde")
DB_USER = os.getenv("DB_USER", "clyde")
DB_PASSWORD = os.getenv("DB_PASSWORD", "clyde")
DB_SSL = os.getenv("DB_SSL", "disable")
JWT_SECRET = os.getenv("JWT_SECRET", "change-me-shared-with-django")
JWT_ALGORITHM = os.getenv("JWT_ALGORITHM", "HS256")
FERNET_KEY = os.getenv("FERNET_KEY", "")
API_PREFIX = os.getenv("API_PREFIX", "/api/v1")
APP_HOST = os.getenv("HOST", "0.0.0.0")
APP_PORT = int(os.getenv("PORT", "8000"))

TEST_USER_ID = 1
BASE_URL = f"http://localhost:{APP_PORT}{API_PREFIX}"


def make_jwt(user_id: int) -> str:
    now = datetime.now(UTC)
    payload = {
        "user_id": user_id,
        "username": "e2e_tester",
        "email": "e2e@test.local",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=7)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def encrypt_token(plaintext: str, fernet_key: str) -> str:
    f = Fernet(fernet_key.encode())
    return f.encrypt(plaintext.encode()).decode()


async def seed_database(
    *,
    github_token_encrypted: str,
    repo_url: str,
    default_branch: str,
) -> tuple[str, str]:
    """Insert test data directly into the database. Returns (project_id, repo_id)."""
    import asyncpg

    ssl_mode = DB_SSL if DB_SSL != "disable" else None
    conn = await asyncpg.connect(
        host=DB_HOST,
        port=DB_PORT,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        ssl=ssl_mode,
    )

    try:
        # Upsert OAuth credential for the test user.
        cred_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO user_oauth_credentials (id, user_id, provider, token_encrypted, scopes, granted_at, created_at, updated_at)
            VALUES ($1, $2, 'github', $3, 'repo,workflow', now(), now(), now())
            ON CONFLICT (user_id, provider)
            DO UPDATE SET token_encrypted = $3, updated_at = now()
            """,
            uuid.UUID(cred_id),
            TEST_USER_ID,
            github_token_encrypted,
        )
        print(f"  GitHub token saved for user_id={TEST_USER_ID}")

        # Create a project.
        project_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO projects (id, user_id, name, description, created_at, updated_at)
            VALUES ($1, $2, $3, $4, now(), now())
            ON CONFLICT DO NOTHING
            """,
            uuid.UUID(project_id),
            TEST_USER_ID,
            f"e2e-test-{datetime.now(UTC).strftime('%H%M%S')}",
            "End-to-end test project created by e2e_setup.py",
        )
        print(f"  Project created: {project_id}")

        # Attach the repo.
        repo_name = repo_url.rstrip("/").rsplit("/", 1)[-1].removesuffix(".git")
        repo_id = str(uuid.uuid4())
        await conn.execute(
            """
            INSERT INTO project_repos (id, project_id, provider, url, default_branch, created_at, updated_at)
            VALUES ($1, $2, 'github', $3, $4, now(), now())
            """,
            uuid.UUID(repo_id),
            uuid.UUID(project_id),
            repo_url,
            default_branch,
        )
        print(f"  Repo attached: {repo_name} ({repo_url})")

        return project_id, repo_id

    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed test data for e2e pipeline run")
    parser.add_argument("--github-token", required=True, help="GitHub Personal Access Token (ghp_...)")
    parser.add_argument("--repo", required=True, help="GitHub repo URL (https://github.com/owner/repo)")
    parser.add_argument("--branch", default="main", help="Default branch (default: main)")
    parser.add_argument("--prompt", default="Analyze this repository and suggest improvements", help="Task description")
    args = parser.parse_args()

    # Resolve Fernet key.
    fernet_key = FERNET_KEY
    if not fernet_key:
        fernet_key = Fernet.generate_key().decode()
        print(f"\n  FERNET_KEY was not set. Generated one for this run:")
        print(f"  {fernet_key}")
        print(f"  Add it to your .env to reuse: FERNET_KEY={fernet_key}\n")

    print("\n=== Step 1: Generating JWT ===")
    token = make_jwt(TEST_USER_ID)
    print(f"  JWT for user_id={TEST_USER_ID} (valid 7 days)")

    print("\n=== Step 2: Encrypting GitHub PAT ===")
    encrypted = encrypt_token(args.github_token, fernet_key)
    print(f"  Encrypted ({len(encrypted)} chars)")

    print("\n=== Step 3: Seeding database ===")
    project_id, repo_id = asyncio.run(
        seed_database(
            github_token_encrypted=encrypted,
            repo_url=args.repo,
            default_branch=args.branch,
        )
    )

    print("\n=== Step 4: Ready! ===")
    print("\nCopy-paste these commands:\n")

    task_body = json.dumps({
        "project_id": project_id,
        "description": args.prompt,
    })

    print("# Create a task (starts the pipeline automatically):")
    print(f'curl -s -X POST {BASE_URL}/tasks \\')
    print(f'  -H "Authorization: Bearer {token}" \\')
    print(f'  -H "Content-Type: application/json" \\')
    print(f"  -d '{task_body}' | python -m json.tool")

    print()
    print("# After you get the task ID from the response, poll its status:")
    print(f'curl -s {BASE_URL}/tasks/TASK_ID_HERE \\')
    print(f'  -H "Authorization: Bearer {token}" | python -m json.tool')

    print()
    print("# List all tasks:")
    print(f'curl -s "{BASE_URL}/tasks?project_id={project_id}" \\')
    print(f'  -H "Authorization: Bearer {token}" | python -m json.tool')

    print()
    print("# Watch pipeline events via WebSocket (wscat or websocat):")
    print(f"wscat -c 'ws://localhost:{APP_PORT}{API_PREFIX}/ws/tasks/TASK_ID_HERE?token={token}'")

    print()
    print("# Check health:")
    print(f"curl -s {BASE_URL}/health | python -m json.tool")


if __name__ == "__main__":
    main()
