"""Google Workspace skill tools for the in-process ``clyde_google`` MCP server.

All tools receive a Google OAuth access token at construction time and call
the Gmail and Calendar REST APIs directly — bypassing Google's hosted MCP
endpoints which have known OAuth client compatibility issues.

Tools exposed:
  Gmail:
    - search_emails      — search inbox threads by query
    - get_email          — fetch a specific thread by ID
    - create_draft       — create a draft email
  Calendar:
    - create_calendar_event   — create an event with optional attendees
    - list_calendar_events    — list upcoming events in a date range
    - create_meet_meeting     — create an event with a Google Meet link
"""

from __future__ import annotations

import json
from typing import Any, ClassVar

import httpx

from src.agent_tools.custom_tools.mcp_server_builder import BaseSkillTool

_GMAIL_BASE = "https://gmail.googleapis.com/gmail/v1/users/me"
_CALENDAR_BASE = "https://www.googleapis.com/calendar/v3"


def _ok(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}]}


def _err(text: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": text}], "isError": True}


# ── Gmail ─────────────────────────────────────────────────────────────────────


class SearchEmailsTool(BaseSkillTool):
    name: ClassVar[str] = "search_emails"
    description: ClassVar[str] = (
        "Search Gmail inbox threads. Returns a list of thread snippets matching the query. "
        "Use standard Gmail search syntax e.g. 'from:someone@example.com subject:meeting'."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Gmail search query string"},
            "max_results": {"type": "integer", "description": "Maximum number of threads to return (default 10)", "default": 10},
        },
        "required": ["query"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        query = args["query"]
        max_results = args.get("max_results", 10)
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_GMAIL_BASE}/threads",
                headers=headers,
                params={"q": query, "maxResults": max_results},
                timeout=15.0,
            )
            if r.status_code != 200:
                return _err(f"Gmail API error {r.status_code}: {r.text[:300]}")
            data = r.json()
            threads = data.get("threads", [])
            if not threads:
                return _ok("No threads found.")
            lines = [f"Found {len(threads)} thread(s):"]
            for t in threads:
                lines.append(f"  - id={t['id']} snippet={t.get('snippet', '')[:120]}")
            return _ok("\n".join(lines))


class GetEmailTool(BaseSkillTool):
    name: ClassVar[str] = "get_email"
    description: ClassVar[str] = (
        "Fetch the full content of a Gmail thread by its thread ID. "
        "Returns sender, subject, date, and body of each message in the thread."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "thread_id": {"type": "string", "description": "Gmail thread ID"},
        },
        "required": ["thread_id"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        thread_id = args["thread_id"]
        headers = {"Authorization": f"Bearer {self._token}"}
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_GMAIL_BASE}/threads/{thread_id}",
                headers=headers,
                params={"format": "metadata", "metadataHeaders": ["From", "Subject", "Date"]},
                timeout=15.0,
            )
            if r.status_code != 200:
                return _err(f"Gmail API error {r.status_code}: {r.text[:300]}")
            thread = r.json()
            lines = []
            for msg in thread.get("messages", []):
                hdrs = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                lines.append(
                    f"From: {hdrs.get('From', '?')} | Date: {hdrs.get('Date', '?')}\n"
                    f"Subject: {hdrs.get('Subject', '?')}\n"
                    f"Snippet: {msg.get('snippet', '')}"
                )
            return _ok("\n\n".join(lines) if lines else "No messages found.")


class CreateDraftTool(BaseSkillTool):
    name: ClassVar[str] = "create_draft"
    description: ClassVar[str] = (
        "Create a draft email in Gmail. The draft will appear in the Drafts folder "
        "for the user to review and send manually."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient email address"},
            "subject": {"type": "string", "description": "Email subject line"},
            "body": {"type": "string", "description": "Plain text email body"},
            "cc": {"type": "string", "description": "CC email address (optional)"},
        },
        "required": ["to", "subject", "body"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        import base64
        to = args["to"]
        subject = args["subject"]
        body = args["body"]
        cc = args.get("cc", "")

        headers_str = f"To: {to}\r\nSubject: {subject}\r\n"
        if cc:
            headers_str += f"Cc: {cc}\r\n"
        headers_str += f"Content-Type: text/plain; charset=utf-8\r\n\r\n"
        raw = base64.urlsafe_b64encode((headers_str + body).encode()).decode()

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_GMAIL_BASE}/drafts",
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                json={"message": {"raw": raw}},
                timeout=15.0,
            )
            if r.status_code not in (200, 201):
                return _err(f"Gmail API error {r.status_code}: {r.text[:300]}")
            draft_id = r.json().get("id")
            return _ok(f"Draft created successfully (id={draft_id}). It is ready in the Gmail Drafts folder for review and sending.")


# ── Calendar ──────────────────────────────────────────────────────────────────


class ListCalendarEventsTool(BaseSkillTool):
    name: ClassVar[str] = "list_calendar_events"
    description: ClassVar[str] = (
        "List upcoming Google Calendar events in a time range. "
        "Returns event titles, times, attendees, and Meet links if present."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "time_min": {"type": "string", "description": "Start of range in RFC3339 format e.g. '2026-05-20T00:00:00Z'"},
            "time_max": {"type": "string", "description": "End of range in RFC3339 format e.g. '2026-05-27T00:00:00Z'"},
            "max_results": {"type": "integer", "description": "Max events to return (default 10)", "default": 10},
        },
        "required": ["time_min", "time_max"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                f"{_CALENDAR_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {self._token}"},
                params={
                    "timeMin": args["time_min"],
                    "timeMax": args["time_max"],
                    "maxResults": args.get("max_results", 10),
                    "singleEvents": "true",
                    "orderBy": "startTime",
                },
                timeout=15.0,
            )
            if r.status_code != 200:
                return _err(f"Calendar API error {r.status_code}: {r.text[:300]}")
            events = r.json().get("items", [])
            if not events:
                return _ok("No events found in the given range.")
            lines = []
            for e in events:
                start = e.get("start", {}).get("dateTime", e.get("start", {}).get("date", "?"))
                attendees = ", ".join(a["email"] for a in e.get("attendees", []))
                meet = e.get("hangoutLink", "")
                line = f"• {e.get('summary', 'No title')} — {start}"
                if attendees:
                    line += f"\n  Attendees: {attendees}"
                if meet:
                    line += f"\n  Meet: {meet}"
                lines.append(line)
            return _ok("\n\n".join(lines))


class CreateCalendarEventTool(BaseSkillTool):
    name: ClassVar[str] = "create_calendar_event"
    description: ClassVar[str] = (
        "Create a Google Calendar event with optional attendees. "
        "Times must be in RFC3339 format e.g. '2026-05-20T10:00:00Z'."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Event title"},
            "start_time": {"type": "string", "description": "Start time in RFC3339 format"},
            "end_time": {"type": "string", "description": "End time in RFC3339 format"},
            "description": {"type": "string", "description": "Event description (optional)"},
            "attendee_emails": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses (optional)",
            },
            "location": {"type": "string", "description": "Event location (optional)"},
        },
        "required": ["summary", "start_time", "end_time"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "summary": args["summary"],
            "start": {"dateTime": args["start_time"], "timeZone": "UTC"},
            "end": {"dateTime": args["end_time"], "timeZone": "UTC"},
        }
        if args.get("description"):
            body["description"] = args["description"]
        if args.get("location"):
            body["location"] = args["location"]
        if args.get("attendee_emails"):
            body["attendees"] = [{"email": e} for e in args["attendee_emails"]]

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_CALENDAR_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                json=body,
                timeout=15.0,
            )
            if r.status_code not in (200, 201):
                return _err(f"Calendar API error {r.status_code}: {r.text[:300]}")
            event = r.json()
            return _ok(
                f"Event created: '{event.get('summary')}'\n"
                f"Start: {event.get('start', {}).get('dateTime')}\n"
                f"Link: {event.get('htmlLink')}"
            )


class CreateMeetMeetingTool(BaseSkillTool):
    name: ClassVar[str] = "create_meet_meeting"
    description: ClassVar[str] = (
        "Create a Google Calendar event with a Google Meet video link. "
        "Returns the Meet URL along with the event details. "
        "Times must be in RFC3339 format e.g. '2026-05-20T10:00:00Z'."
    )
    input_schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "summary": {"type": "string", "description": "Meeting title"},
            "start_time": {"type": "string", "description": "Start time in RFC3339 format"},
            "end_time": {"type": "string", "description": "End time in RFC3339 format"},
            "description": {"type": "string", "description": "Meeting description (optional)"},
            "attendee_emails": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of attendee email addresses",
            },
        },
        "required": ["summary", "start_time", "end_time"],
    }

    def __init__(self, google_token: str) -> None:
        self._token = google_token

    async def run(self, args: dict[str, Any]) -> dict[str, Any]:
        import uuid
        body: dict[str, Any] = {
            "summary": args["summary"],
            "start": {"dateTime": args["start_time"], "timeZone": "UTC"},
            "end": {"dateTime": args["end_time"], "timeZone": "UTC"},
            "conferenceData": {
                "createRequest": {
                    "requestId": str(uuid.uuid4()),
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
        }
        if args.get("description"):
            body["description"] = args["description"]
        if args.get("attendee_emails"):
            body["attendees"] = [{"email": e} for e in args["attendee_emails"]]

        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{_CALENDAR_BASE}/calendars/primary/events",
                headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                params={"conferenceDataVersion": "1"},
                json=body,
                timeout=15.0,
            )
            if r.status_code not in (200, 201):
                return _err(f"Calendar API error {r.status_code}: {r.text[:300]}")
            event = r.json()
            meet_link = event.get("hangoutLink", "Meet link pending — check Calendar")
            return _ok(
                f"Meeting created: '{event.get('summary')}'\n"
                f"Start: {event.get('start', {}).get('dateTime')}\n"
                f"Google Meet: {meet_link}\n"
                f"Calendar link: {event.get('htmlLink')}"
            )
