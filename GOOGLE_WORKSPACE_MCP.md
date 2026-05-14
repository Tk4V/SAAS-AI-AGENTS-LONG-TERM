# Google Workspace MCP — Feasibility & Research

## TL;DR

Yes, we can do this. Google provides **official hosted MCP endpoints** for Gmail, Calendar, Drive, Chat, and People — callable via HTTP with an OAuth Bearer token, the same pattern as Slack and Jira. Google APIs are free to use. No extra service to deploy, no stdio.

---

## Official Google MCP Endpoints

| Service | Endpoint | Transport |
|---------|----------|-----------|
| Gmail | `https://gmailmcp.googleapis.com/mcp/v1` | HTTP |
| Google Calendar | `https://calendarmcp.googleapis.com/mcp/v1` | HTTP |
| Google Drive | `https://drivemcp.googleapis.com/mcp/v1` | HTTP |
| Google Chat | `https://chatmcp.googleapis.com/mcp/v1` | HTTP |
| People (Contacts) | `https://people.googleapis.com/mcp/v1` | HTTP |

Auth: `Authorization: Bearer <google_oauth_access_token>`

These are the same kind of hosted MCP endpoints as Slack (`mcp.slack.com/mcp`) and Jira (`mcp.atlassian.com/v1/mcp/authv2`). The user's Google OAuth access token is passed as the Bearer header per request. Token refresh is handled by the existing `OAuthRefresher` in Clyde.

---

## What We Can Do (Tool Capabilities)

### Gmail
| Tool | Description |
|------|-------------|
| `send_email` | Send an email (to, subject, body, cc/bcc) |
| `create_draft` | Create a draft for user review |
| `search_threads` | Search inbox with a query string |
| `get_thread` | Fetch a specific email thread |
| `list_labels` / `label_message` | Manage Gmail labels |

### Google Calendar
| Tool | Description |
|------|-------------|
| `create_event` | Create an event (title, start/end, attendees, description, location) |
| `list_events` | List upcoming events in a date range |
| `update_event` | Update an existing event |
| `delete_event` | Delete/cancel an event |
| `respond_to_event` | Accept, decline, or tentatively accept an invitation |
| `suggest_time` | Suggest available times using Free/Busy API |

### Google Drive
| Tool | Description |
|------|-------------|
| Search files | Find files by name or content |
| Read file content | Read Docs, Sheets, or plain files |
| Manage permissions | Share or restrict access |

### Google Chat
| Tool | Description |
|------|-------------|
| Search messages | Search across conversations |
| Send messages | Post to a Chat space |

### People (Contacts)
| Tool | Description |
|------|-------------|
| Search contacts | Look up by name or email |
| Get profile | Fetch contact details |

---

## Is It Free?

| Item | Cost |
|------|------|
| Google Cloud project + OAuth app | **Free** |
| Gmail API | **Free** |
| Google Calendar API | **Free** |
| Google Drive API | **Free** |
| Google Chat API | **Free** |
| People API | **Free** |
| Google Workspace subscription | **Not required** — personal `@gmail.com` accounts work; Workspace plans ($7+/user/month) only needed for org-level admin features |
| Official Google MCP endpoints | **Free** |

**Bottom line:** Zero cost. The APIs and MCP endpoints are free; only infrastructure/hosting costs apply (which are already covered by the existing Clyde backend).

---

## Transport Options

| Transport | Status | Notes |
|-----------|--------|-------|
| **HTTP (Streamable)** | **Recommended** | Used by the official Google MCP endpoints; same as Slack/Jira in Clyde |
| SSE | Deprecated | Superseded by Streamable HTTP |
| Stdio | Avoid | Not scalable — ruled out |

---

## Integration Approach

The official endpoints accept `Authorization: Bearer <token>` over HTTP — exactly the same pattern as Slack and Jira in Clyde today. No custom proxy, no extra Docker service.

**What needs to happen:**
1. Add Gmail and Calendar scopes to `src/integrations/google/config.py` so the OAuth token has the right permissions
2. Add `mcp_server_configs` DB rows for each service (gmail, calendar, drive, etc.) — same as the existing GitHub/Slack/Jira rows
3. Create `src/agent_tools/mcp/google.py` factory function(s) that pass `Authorization: Bearer <token>`
4. Seed `agent_tool_configs` rows for `mcp__gmail__*`, `mcp__calendar__*`, etc.
5. Users re-authorize Google to grant the new workspace scopes

**OAuth scopes required:**
```
# Gmail
https://www.googleapis.com/auth/gmail.send
https://www.googleapis.com/auth/gmail.readonly
https://www.googleapis.com/auth/gmail.compose

# Calendar
https://www.googleapis.com/auth/calendar
https://www.googleapis.com/auth/calendar.events

# Drive (optional)
https://www.googleapis.com/auth/drive.readonly

# People (optional)
https://www.googleapis.com/auth/contacts.readonly
```

The existing Google OAuth config already uses `access_type=offline` + `prompt=consent`, so refresh tokens are issued and the existing `OAuthRefresher` handles token rotation automatically.

---

## What Clyde Agents Will Be Able to Do

- **Send emails** — "send a follow-up to the team about today's standup"
- **Schedule meetings** — "set up a 1:1 with John next Tuesday at 2pm, invite john@company.com"
- **Search emails** — "find all emails from the client about the contract"
- **Create drafts** for user review before sending
- **Update or cancel events** — "move tomorrow's meeting to Thursday"
- **Suggest meeting times** — "find a free 30-minute slot for the team this week"
- **Look up contacts** — "what's Sarah's email address?"
