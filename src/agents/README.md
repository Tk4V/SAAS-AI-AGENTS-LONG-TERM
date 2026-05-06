# Agents — tools, configs, customization

How Clyde agents pick their tools, how users fork the defaults, and where
each piece of state actually lives. Read this before adding a tool, a
provider, or a config UI.

## The model

Three sources, each in its natural home:

| Source | What it holds | Where it lives |
|---|---|---|
| **System tools** | SDK builtins (`Read`, `Edit`, `Write`, `Glob`, `Grep`, `Agent`, `Bash(...)`). Invariant — never user-configurable. | `SDKAgent.SYSTEM_TOOLS` ClassVar |
| **Integrations** | MCP-backed providers (`github`, `jira`, `slack`, `aws`, ...). Each declares its tool pattern (`mcp__github__*`). | `src/integrations/_shared/registry.py` (`ProviderCatalog`) |
| **Custom tool groups** | In-process `@tool` SDK servers we own (e.g. `clyde_git`). Each declares display metadata and tool patterns. | `src/agent_tools/` (custom registry) |

User customization (which integrations, which custom groups) lives in DB.
Tool definitions never do.

## Why nothing about tools is in the database

A `tools` catalog table was the obvious move and it's the wrong move.

- Tool patterns must be declared in code anyway — the SDK and MCP servers
  consume them at runtime. Putting them in DB just duplicates a fact that
  already lives next to the code that uses it.
- Drift becomes a problem the moment you have two sources. Adding a new
  provider in code without re-running a seed = silently broken UI.
- Per-tool granularity (e.g. allow `mcp__github__create_pr` but block
  `mcp__github__delete_repo`) is a poor UX (50 toggles per provider) and
  fights the MCP discovery model anyway. Bundle-level is the right
  gran­ularity, and bundles are providers / custom groups.

So: **provider catalog and custom registry are the catalog.** The DB only
records the user's selections.

## What's in the database

```
agent_configs                — a user's named fork of a template
agent_config_versions        — versioned snapshot of one config
```

`agent_configs`:
- `id`, `user_id`, `template_name` (`orchestrator` | `publisher`),
  `name`, `current_version_id`

`agent_config_versions`:
- `id`, `config_id`, `version_number`, `created_at`
- `enabled_providers: ProviderKind[]`
- `enabled_custom_groups: text[]`
- `system_prompt_override: text | null`
- `subagents_overrides: jsonb | null`

Each save creates a new version row; `current_version_id` flips. Rollback
= flip the pointer.

`agent_tool_configs` and `user_tool_configs` are deprecated and dropped.

## Runtime composition

When the pipeline runner instantiates an agent for a task:

```python
version = await config_repo.get_active_version(user_id, template_name)

patterns = list(SDKAgent.SYSTEM_TOOLS)
for provider in version.enabled_providers:
    if await credentials.user_has(provider, user_id=user_id):
        patterns.append(provider_catalog[provider].tool_pattern)
for group_key in version.enabled_custom_groups:
    patterns.extend(custom_registry[group_key].tool_patterns)
```

The agent sees the resolved list as a per-instance override of
`SDK_ALLOWED_TOOLS`. The class-level ClassVar stays as the fallback for
unconfigured users (default orchestrator + publisher).

## The `/tools` endpoint

Returns the dropdown contents — built from the two code registries at
request time. No DB read for tool definitions.

```json
{
  "integrations": [
    {
      "key": "github",
      "display_name": "GitHub",
      "description": "...",
      "requires_credential": true,
      "connected": true
    }
  ],
  "custom": [
    {"key": "clyde_git", "display_name": "Git operations", "description": "..."}
  ]
}
```

System tools never appear because they aren't in either registry. The
frontend gets a clean list with no filtering of its own.

## Adding things

**Adding a new MCP provider:** see `src/integrations/README.md`. The
provider's tool pattern lands in `GET /tools` automatically once it's in
`ProviderCatalog`.

**Adding a custom tool group:** declare an entry in the custom registry
(`src/agent_tools/...`) with `key`, `display_name`, `description`,
`tool_patterns`. Register the underlying `@tool`-decorated SDK server
alongside. It appears in `GET /tools` next request.

**Adding a system tool:** edit `SDKAgent.SYSTEM_TOOLS`. There is no UI
surface for these — by design.

**Adding an agent template:** the only two templates today are
`orchestrator` and `publisher`. New templates are a code change (new
`SDKAgent` subclass) plus an enum value used by `agent_configs.template_name`.

## What does *not* belong here

- **Routes:** `src/api/views/`. They call the config service, which loads
  the version and hands resolved patterns to the agent.
- **Credential resolution:** `src/credentials/`. The runtime checks
  `user_has(provider)` before mounting an MCP server — agents don't see
  encrypted tokens.
- **MCP server factories:** still in `src/integrations/<provider>/` for
  external providers and `src/agent_tools/` for in-process ones. The
  registries point at them, they don't replace them.
