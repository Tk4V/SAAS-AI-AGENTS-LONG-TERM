#!/usr/bin/env python3
"""Test Clyde's in-process Google Workspace tools directly.

Tests the same tool logic as the clyde_google MCP server by calling the
Google REST APIs with the stored OAuth token — same code path the agent uses.

Run with:
    python scripts/test_google_workspace_mcp.py
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

CRED_ID = "0bc9f118-b98e-4242-9c05-af9f3a0c71b7"
ATTENDEE_1 = "tsdan51@gmail.com"
ATTENDEE_2 = "miyukitsukishima@gmail.com"

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


async def get_fresh_token() -> str:
    conn = await psycopg.AsyncConnection.connect(DB_URL)
    async with conn:
        cur = await conn.execute("SELECT encrypted_payload FROM credentials WHERE id = %s", (CRED_ID,))
        row = await cur.fetchone()
    payload = json.loads(Fernet(FERNET_KEY.encode()).decrypt(row[0].encode()).decode())
    refresh_token = payload.get("refresh_token")
    async with httpx.AsyncClient() as client:
        r = await client.post("https://oauth2.googleapis.com/token", data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": os.environ["GOOGLE_OAUTH_CLIENT_ID"],
            "client_secret": os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        })
        return r.json()["access_token"]


async def main() -> None:
    print("Refreshing token...")
    token = await get_fresh_token()
    print(f"Token: {token[:12]}...\n")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient() as client:

        # ── 1. Search emails ──────────────────────────────────────────────────
        print("=== [1/4] Search last 5 inbox emails ===")
        r = await client.get(f"{_GMAIL_BASE}/threads", headers=headers, params={"q": "in:inbox", "maxResults": 5})
        threads = r.json().get("threads", [])
        for t in threads:
            print(f"  {t['id']} — {t.get('snippet','')[:80]}")
        print()

        # ── 2. Create draft email ─────────────────────────────────────────────
        import base64
        print(f"=== [2/4] Create draft email to {ATTENDEE_2} ===")
        raw = base64.urlsafe_b64encode(
            f"To: {ATTENDEE_2}\r\nSubject: Hey from Clyde!\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n"
            f"Hi,\n\nThis draft was created by Clyde via the Google Workspace integration.\n\nBest,\nClyde".encode()
        ).decode()
        r = await client.post(f"{_GMAIL_BASE}/drafts", headers=headers, json={"message": {"raw": raw}})
        print(f"  Status: {r.status_code} — Draft ID: {r.json().get('id')}\n")

        # ── 3. Create calendar event ──────────────────────────────────────────
        print(f"=== [3/4] Create calendar event for {ATTENDEE_1} + {ATTENDEE_2} ===")
        r = await client.post(
            f"{_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            json={
                "summary": "Clyde Test Sync",
                "description": "Test event created via Clyde Google Workspace integration.",
                "start": {"dateTime": "2026-05-20T10:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-05-20T11:00:00Z", "timeZone": "UTC"},
                "attendees": [{"email": ATTENDEE_1}, {"email": ATTENDEE_2}],
            },
        )
        event = r.json()
        print(f"  Status: {r.status_code} — Event: {event.get('summary')} | Link: {event.get('htmlLink','')}\n")

        # ── 4. Create Google Meet meeting ─────────────────────────────────────
        import uuid
        print(f"=== [4/4] Create Google Meet meeting for {ATTENDEE_1} + {ATTENDEE_2} ===")
        r = await client.post(
            f"{_CALENDAR_BASE}/calendars/primary/events",
            headers=headers,
            params={"conferenceDataVersion": "1"},
            json={
                "summary": "Clyde Test — Google Meet",
                "description": "Test meeting with Google Meet link via Clyde.",
                "start": {"dateTime": "2026-05-20T14:00:00Z", "timeZone": "UTC"},
                "end": {"dateTime": "2026-05-20T15:00:00Z", "timeZone": "UTC"},
                "attendees": [{"email": ATTENDEE_1}, {"email": ATTENDEE_2}],
                "conferenceData": {
                    "createRequest": {
                        "requestId": str(uuid.uuid4()),
                        "conferenceSolutionKey": {"type": "hangoutsMeet"},
                    }
                },
            },
        )
        event = r.json()
        meet_link = event.get("hangoutLink", "pending")
        print(f"  Status: {r.status_code} — Meet: {meet_link} | Link: {event.get('htmlLink','')}\n")

    print("Done.")


asyncio.run(main())
