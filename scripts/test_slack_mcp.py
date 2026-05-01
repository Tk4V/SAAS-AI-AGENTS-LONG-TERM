#!/usr/bin/env python3
"""One-off diagnostic: test whether our xoxb- bot token is accepted by mcp.slack.com/mcp.

Run with:
    python scripts/test_slack_mcp.py

Delete after use — this is a debugging tool only.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import psycopg
from cryptography.fernet import Fernet
from dotenv import load_dotenv

load_dotenv(Path(__file__).parents[1] / ".env")

FERNET_KEY = os.environ["FERNET_KEY"]
DB_URL = (
    f"postgresql://{os.environ.get('DB_USER', 'clyde')}:{os.environ.get('DB_PASSWORD', 'clyde')}"
    f"@{os.environ.get('DB_HOST', 'localhost')}:{os.environ.get('DB_PORT', '5432')}"
    f"/{os.environ.get('DB_NAME', 'clyde')}"
)
CRED_ID = "your cread id"


async def main() -> None:
    # 1. Fetch encrypted payload from DB
    conn = await psycopg.AsyncConnection.connect(DB_URL)
    async with conn:
        cur = await conn.execute(
            "SELECT encrypted_payload FROM credentials WHERE id = %s", (CRED_ID,)
        )
        row = await cur.fetchone()

    if not row:
        print("Credential not found in DB")
        sys.exit(1)

    # 2. Decrypt
    fernet = Fernet(FERNET_KEY.encode())
    plaintext = fernet.decrypt(row[0].encode()).decode()
    payload = json.loads(plaintext)
    token = payload["access_token"]
    print(f"Token prefix: {token[:12]}...")

    # 3. Test against Slack MCP server
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://mcp.slack.com/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2024-11-05",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "1"},
                },
            },
            timeout=10.0,
        )

    print(f"\nStatus: {response.status_code}")
    print(f"Content-Type: {response.headers.get('content-type')}")
    print(f"www-authenticate: {response.headers.get('www-authenticate', '(none)')}")
    print(f"Body: {response.text[:2000]}")


asyncio.run(main())
