#!/usr/bin/env python3
"""Test script: create Go + JS files in FinanceAppDesktop, assign a Jira ticket to
Danylo Tsebro, and notify Slack user U0AT1Q094PQ with the PR and Jira links.

Automatically approves any pending tool approvals via WebSocket
(the only supported resolution path).

Prerequisites:
    1. App running:  docker compose -f docker-compose-local.yaml up
    2. Migrations applied (migrate container completes successfully)
    3. User has GitHub, Jira and Slack credentials stored in the DB

Usage:
    # Create a fresh project + task (default)
    python scripts/test_finance_app_task.py

    # Reuse an existing project (skips project creation)
    python scripts/test_finance_app_task.py --project-id <uuid>

    # Override the user that owns the task
    python scripts/test_finance_app_task.py --user-id 7
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta

import httpx
import jwt as pyjwt
import websockets
from dotenv import load_dotenv

load_dotenv()

# ── tunables ──────────────────────────────────────────────────────────────────
REPO_URL        = "https://github.com/DanyloTs/FinanceAppDesktop"
POLL_INTERVAL   = 5           # seconds between status polls
MAX_POLL        = 180         # 15 minutes ceiling
TERMINAL        = {"completed", "awaiting_ci", "needs_human", "failed"}

TASK_PROMPT = """\
Work on the repository https://github.com/DanyloTs/FinanceAppDesktop and complete \
all three steps below in order. Do not stop until all three are done.

── STEP 1: CREATE FILES AND OPEN A PR ──────────────────────────────────────────
Create a new branch called `feature/add-analytics-utils` from main and add these \
two files:

File 1 — `internal/analytics/revenue_calculator.go`:
```go
package analytics

// Transaction represents a single financial transaction.
type Transaction struct {
    ID     string
    Amount float64
}

// RevenueCalculator computes revenue from a list of transactions.
type RevenueCalculator struct{}

// New returns a new RevenueCalculator.
func New() *RevenueCalculator {
    return &RevenueCalculator{}
}

// Calculate sums the Amount fields of all provided transactions.
func (r *RevenueCalculator) Calculate(txs []Transaction) float64 {
    total := 0.0
    for _, t := range txs {
        total += t.Amount
    }
    return total
}
```

File 2 — `src/utils/formatCurrency.js`:
```js
/**
 * Format a numeric amount as a localised currency string.
 * @param {number} amount
 * @param {string} currency - ISO 4217 code, e.g. "USD"
 * @param {string} [locale="en-US"]
 * @returns {string}
 */
export function formatCurrency(amount, currency, locale = "en-US") {
  return new Intl.NumberFormat(locale, {
    style: "currency",
    currency,
  }).format(amount);
}

/**
 * Parse a localised currency string back to a number.
 * Strips all non-numeric characters except decimal separators.
 * @param {string} str
 * @returns {number}
 */
export function parseCurrency(str) {
  return parseFloat(str.replace(/[^0-9.-]/g, ""));
}
```

Commit both files with message "feat: add revenue calculator (Go) and currency \
formatter (JS)", push the branch, then open a Pull Request against main with title \
"feat: add analytics utilities (Go + JS)". Capture the full PR URL — you will need \
it in steps 2 and 3.

── STEP 2: CREATE AND ASSIGN A JIRA TICKET ─────────────────────────────────────
Using the Jira MCP:
1. List available projects with jira_get_all_projects and pick the most relevant one.
2. Create a new issue with:
   - Summary: "Add analytics utilities (Go + JS) to FinanceAppDesktop"
   - Description: Include the PR URL from Step 1 and a brief explanation of the two \
new files.
   - Assignee: Danylo Tsebro (search for this user with jira_search_users if needed)
3. Verify the issue was created by calling jira_get_issue with the returned key.
Capture the full Jira issue key (e.g. PROJ-123) — you need it in Step 3.

── STEP 3: NOTIFY VIA SLACK ────────────────────────────────────────────────────
Send a direct message to Slack user ID U0AT1Q094PQ using the Slack MCP.
Message content (fill in the real URLs/keys from steps 1 and 2):
"New analytics utilities added to FinanceAppDesktop
PR: <PR URL from Step 1>
Jira: <Jira ticket key from Step 2>
Files added: internal/analytics/revenue_calculator.go and src/utils/formatCurrency.js"

── COMPLETION ───────────────────────────────────────────────────────────────────
When all three steps are done, return a summary with:
- PR URL
- Jira ticket key and URL
- Confirmation that the Slack message was sent to U0AT1Q094PQ
"""


# ── helpers ───────────────────────────────────────────────────────────────────

class Log:
    @staticmethod
    def _ts() -> str:
        return datetime.now(UTC).strftime("%H:%M:%S")

    @classmethod
    def info(cls, msg: str) -> None:
        print(f"[{cls._ts()}] {msg}")

    @classmethod
    def ok(cls, msg: str) -> None:
        print(f"[{cls._ts()}] \033[92m✓ {msg}\033[0m")

    @classmethod
    def warn(cls, msg: str) -> None:
        print(f"[{cls._ts()}] \033[93m⚠ {msg}\033[0m")

    @classmethod
    def fail(cls, msg: str) -> None:
        print(f"[{cls._ts()}] \033[91m✗ {msg}\033[0m")

    @classmethod
    def section(cls, title: str) -> None:
        print()
        print("─" * 60)
        print(f"  {title}")
        print("─" * 60)


class Config:
    def __init__(self, user_id: int | None = None) -> None:
        self.jwt_secret    = os.getenv("JWT_SECRET", "change-me-shared-with-django")
        self.jwt_algorithm = os.getenv("JWT_ALGORITHM", "HS256")
        self.jwt_audience  = os.getenv("JWT_AUDIENCE", "")
        self.port          = int(os.getenv("PORT", "8000"))
        self.api_prefix    = os.getenv("API_PREFIX", "/api/v1")
        self.user_id       = user_id or int(os.getenv("E2E_USER_ID", "7"))

    @property
    def base_url(self) -> str:
        return f"http://localhost:{self.port}{self.api_prefix}"

    @property
    def ws_base(self) -> str:
        return f"ws://localhost:{self.port}{self.api_prefix}"

    def jwt(self) -> str:
        now = datetime.now(UTC)
        payload: dict = {
            "user_id": self.user_id,
            "username": "test_user",
            "email":    "test@local",
            "iat": int(now.timestamp()),
            "exp": int((now + timedelta(days=1)).timestamp()),
        }
        if self.jwt_audience:
            payload["aud"] = self.jwt_audience
        return pyjwt.encode(payload, self.jwt_secret, algorithm=self.jwt_algorithm)


# ── project / task helpers ────────────────────────────────────────────────────

async def ensure_project(
    http: httpx.AsyncClient,
    cfg: Config,
    headers: dict,
    project_id: str | None,
) -> str:
    if project_id:
        Log.info(f"Reusing project: {project_id}")
        return project_id

    name = f"finance-app-test-{datetime.now(UTC).strftime('%H%M%S')}"
    Log.info(f"Creating project '{name}' with repo {REPO_URL} ...")
    r = await http.post(
        f"{cfg.base_url}/projects",
        headers=headers,
        json={
            "name": name,
            "description": "Test project for FinanceAppDesktop analytics task",
            "repos": [{"url": REPO_URL, "default_branch": "main"}],
        },
    )
    if r.status_code != 201:
        Log.fail(f"Project creation failed: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    pid = r.json()["id"]
    Log.ok(f"Project created: {pid}")
    return pid


async def create_task(
    http: httpx.AsyncClient,
    cfg: Config,
    headers: dict,
    project_id: str,
) -> str:
    Log.info("Creating task ...")
    r = await http.post(
        f"{cfg.base_url}/tasks",
        headers=headers,
        json={"project_id": project_id, "description": TASK_PROMPT},
    )
    if r.status_code != 201:
        Log.fail(f"Task creation failed: {r.status_code} {r.text[:300]}")
        sys.exit(1)
    tid = r.json()["id"]
    Log.ok(f"Task created: {tid}")
    return tid


# ── WebSocket approval handler ────────────────────────────────────────────────

async def resolve_approvals_via_ws(cfg: Config, token: str, task_id: str, resolved: set[str]) -> None:
    """Open a WebSocket, send approval_response for every pending approval, then close."""
    uri = f"{cfg.ws_base}/ws/tasks/{task_id}/chat?token={token}"
    try:
        async with websockets.connect(uri, open_timeout=10) as ws:  # type: ignore[attr-defined]
            # Fetch pending approvals via HTTP (WS is only for sending the decision)
            async with httpx.AsyncClient(timeout=10.0) as http:
                headers = {"Authorization": f"Bearer {token}"}
                ar = await http.get(
                    f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers
                )
                if ar.status_code != 200:
                    Log.warn(f"  GET approvals {ar.status_code}")
                    return
                pending = [
                    a for a in ar.json()
                    if a["status"] == "pending" and a["id"] not in resolved
                ]

            for approval in pending:
                aid  = approval["id"]
                tool = approval["tool_name"]
                inp  = approval.get("tool_input", {})
                cmd  = inp.get("command", inp.get("input", str(inp))[:80])
                Log.info(f"  Approving [{tool}]: {str(cmd)[:80]}")
                msg = json.dumps({
                    "type":        "approval_response",
                    "approval_id": aid,
                    "approved":    True,
                    "payload":     {},
                })
                await ws.send(msg)
                # Wait briefly for the server ack event
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    evt = json.loads(raw)
                    if evt.get("name") == "task.approval_resolved":
                        Log.ok(f"  Approved: {aid}")
                    else:
                        Log.info(f"  Server event: {evt.get('name', raw[:60])}")
                except asyncio.TimeoutError:
                    Log.warn(f"  No ack within 5s for {aid} — may still have resolved")
                resolved.add(aid)

    except Exception as exc:
        Log.warn(f"  WS resolve error: {exc}")


# ── main poll loop ────────────────────────────────────────────────────────────

async def poll_and_auto_approve(
    http: httpx.AsyncClient,
    cfg: Config,
    headers: dict,
    token: str,
    task_id: str,
) -> str:
    Log.info("Polling ... (all approvals will be auto-approved via WebSocket)")
    last_status = "running"
    resolved: set[str] = set()

    for _ in range(MAX_POLL):
        await asyncio.sleep(POLL_INTERVAL)

        r = await http.get(f"{cfg.base_url}/tasks/{task_id}", headers=headers)
        if r.status_code != 200:
            Log.warn(f"Poll returned {r.status_code}, retrying ...")
            continue

        task   = r.json()
        status = task["status"]

        if status != last_status:
            Log.info(f"  Status: {last_status} → {status}")
            last_status = status

        if status == "awaiting_approval":
            # Check if there are actually pending rows before opening WS
            ar = await http.get(
                f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers
            )
            pending = [
                a for a in (ar.json() if ar.status_code == 200 else [])
                if a["status"] == "pending" and a["id"] not in resolved
            ]
            if not pending:
                Log.info("  (awaiting_approval but no new rows yet, retrying ...)")
                continue

            await resolve_approvals_via_ws(cfg, token, task_id, resolved)

        elif status in TERMINAL:
            return status

    Log.warn("Reached poll ceiling without a terminal status.")
    return last_status


# ── summary ───────────────────────────────────────────────────────────────────

async def print_summary(
    http: httpx.AsyncClient,
    cfg: Config,
    headers: dict,
    task_id: str,
    final_status: str,
) -> None:
    Log.section("Summary")
    color = "\033[92m" if final_status in {"completed", "awaiting_ci"} else "\033[91m"
    print(f"  Final status : {color}{final_status}\033[0m")
    print(f"  Task ID      : {task_id}")
    print(f"  App URL      : {cfg.base_url}/tasks/{task_id}")

    r = await http.get(f"{cfg.base_url}/tasks/{task_id}", headers=headers)
    if r.status_code == 200:
        task = r.json()
        pr_urls = task.get("pr_urls") or {}
        if pr_urls:
            print("  PR URLs:")
            for branch, url in pr_urls.items():
                print(f"    {branch}: {url}")
        err = task.get("error_message")
        if err:
            Log.warn(f"  Error: {err[:300]}")

    ar = await http.get(f"{cfg.base_url}/tasks/{task_id}/approvals", headers=headers)
    if ar.status_code == 200:
        rows = ar.json()
        if rows:
            print(f"  Approvals ({len(rows)}):")
            for row in rows:
                s = row["status"]
                c = "\033[92m" if s == "approved" else ("\033[91m" if s == "denied" else "\033[93m")
                print(f"    {c}{s:8s}\033[0m  {row['tool_name']}")


# ── entry point ───────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> bool:
    cfg     = Config(user_id=args.user_id)
    token   = cfg.jwt()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=30.0) as http:
        r = await http.get(f"{cfg.base_url}/health")
        if r.status_code != 200:
            Log.fail(f"App not reachable ({r.status_code}). Is it running?")
            return False
        Log.ok(f"App healthy: {r.json().get('service')} v{r.json().get('version')}")

        project_id = await ensure_project(http, cfg, headers, args.project_id)
        task_id    = await create_task(http, cfg, headers, project_id)

        final_status = await poll_and_auto_approve(http, cfg, headers, token, task_id)
        await print_summary(http, cfg, headers, task_id, final_status)

    return final_status in {"completed", "awaiting_ci"}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run FinanceAppDesktop analytics task: Go+JS files, Jira ticket, Slack notification"
    )
    parser.add_argument("--project-id", help="Reuse an existing project UUID")
    parser.add_argument("--user-id", type=int, help="User ID (default: E2E_USER_ID or 7)")
    args = parser.parse_args()

    print()
    print("=" * 60)
    print("  Clyde AI — FinanceAppDesktop Analytics Task")
    print("=" * 60)
    print(f"  Repo   : {REPO_URL}")
    print(f"  Goal   : Create Go + JS files, Jira ticket (→ Danylo Tsebro),")
    print(f"           Slack DM to U0AT1Q094PQ")
    print("=" * 60)

    Log.section("Connecting & creating task")
    ok = asyncio.run(run(args))

    print()
    print("─" * 60)
    print(f"  Result: {'PASS' if ok else 'REVIEW LOGS'}")
    print("─" * 60)
    print()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
