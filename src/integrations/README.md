# Integrations

OAuth and API integrations with external providers (GitHub today, Slack /
Discord / Jira / Sentry next, more later). Read this file before adding a
new provider.

## Layout

```
integrations/
├── _shared/          # framework — read-only when adding a provider
├── <provider>/       # one folder per provider — config, quirks, API client
├── __init__.py       # intentionally empty (avoid circular re-exports)
└── README.md         # this file
```

The leading underscore on `_shared/` is a Python convention for "private to
the package". You import from it — you do not edit it when adding a provider.

## How to add a new provider

Goal: from "we want to support ProviderX" to "user can connect their
ProviderX account" with the smallest possible diff. Five steps, ~20 lines
of new code for a standards-compliant OAuth provider.

### 1. Pick a kind

`IntegrationKind` is an alias for the canonical `GitProviderKind` enum that
backs the database column. Renaming the enum is a separate migration; until
that lands, every new provider value also needs the database `git_provider_kind`
type extended via Alembic.

For now: open `src/db/models/project.py` and add the enum value there.
That value becomes accessible as `IntegrationKind.<NAME>` from the
integration layer.

### 2. Add settings keys

Open `src/config/settings.py` and add two `SecretStr` fields:

```python
slack_oauth_client_id: SecretStr = SecretStr("")
slack_oauth_client_secret: SecretStr = SecretStr("")
```

Append the matching variables (empty values) to `.env.example`.

### 3. Create the provider folder

```
mkdir src/integrations/slack
touch src/integrations/slack/__init__.py
touch src/integrations/slack/config.py
```

Copy `github/config.py` as a starting template. Adjust:

- `kind`, `category`, `display_name`
- `authorize_url`, `token_url`, `revoke_url` (from the provider's docs)
- `default_scopes`, `scope_separator` (most providers use space)
- `use_pkce` — leave `True` unless the provider rejects PKCE (GitHub OAuth
  Apps do, GitHub Apps do not)
- `refresh_supported` — `True` if the provider issues refresh tokens
- `token_endpoint_auth_method` — usually `client_secret_post`; check docs
- `client_id_setting`, `client_secret_setting` — the names you added in step 2
- `api_base_url` — for the API client (step 5), if you write one
- `compliance_installer` — `None` unless the provider misbehaves (step 4)
- `custom_revoker` — `None` unless the provider uses a non-RFC-7009 flow
  (GitHub does — see `github/revoker.py`)

### 4. (Only if needed) Add a compliance hook

Some providers do not strictly follow OAuth 2.0. Examples:

- Slack returns `200 OK` with `ok: false` instead of HTTP 4xx on auth errors.
- GitHub returns `application/x-www-form-urlencoded` token responses unless
  you send `Accept: application/json`.
- Atlassian requires a follow-up call to `accessible-resources` to learn the
  cloudId before the API base URL is known.

If your provider is fine, skip this step. Otherwise create
`<provider>/compliance.py` with a function `install_<provider>_hooks(client)`
that registers Authlib hooks (`access_token_response`,
`access_token_request`, `refresh_token_response`, `refresh_token_request`).
See `github/compliance.py` for the smallest possible example.

Wire it into `<provider>/config.py`:

```python
from src.integrations.slack.compliance import install_slack_hooks

SLACK = OAuthProviderConfig(
    ...,
    compliance_installer=install_slack_hooks,
)
```

### 5. Register the provider

Open `_shared/registry.py` and add two lines: an import and an entry in
`_PROVIDERS`:

```python
from src.integrations.slack.config import SLACK

_PROVIDERS = (
    GITHUB,
    SLACK,
)
```

Done. The user can now visit `/auth/oauth/slack/start` and connect their
Slack workspace. The token is stored encrypted in
`user_oauth_credentials`, the same table GitHub uses.

### 6. (Optional, lazy) Add an API client

You only need this when an agent or route actually needs to call the
provider's API. Most providers take their first API integration weeks after
the OAuth flow lights up — that's fine, the connection still works.

When you do need it, create `<provider>/client.py` with a class that
inherits `BaseApiClient`. See `github/client.py` for the pattern. The base
class handles:

- token resolution per request
- `Authorization: Bearer <token>` injection
- relative-vs-absolute URL handling
- 4xx/5xx → `ProviderApiError`, 429 → `ProviderRateLimitError`

You add the typed methods.

### 7. (Optional) Add a custom revoker

If the provider does not implement RFC 7009 token revocation (e.g. GitHub
OAuth Apps, which use `DELETE /applications/{client_id}/token` with Basic
auth), create `<provider>/revoker.py` with an async function that takes the
plaintext access token and revokes it. Wire it into the config:

```python
from src.integrations.slack.revoker import revoke_slack_token

SLACK = OAuthProviderConfig(
    ...,
    custom_revoker=revoke_slack_token,
)
```

`OAuthAdapter.revoke()` dispatches to `custom_revoker` when present, falls
back to RFC 7009 via `revoke_url`, and is a no-op otherwise.

## Files inside `_shared/`

| File | One-line purpose |
|------|------------------|
| `kinds.py` | `IntegrationKind` (alias of `GitProviderKind`) and `IntegrationCategory` enums. |
| `config.py` | `OAuthProviderConfig` dataclass — the shape of every provider declaration. |
| `tokens.py` | `TokenBundle` — normalized OAuth token data (access, refresh, expiry, scopes). |
| `state.py` | `OAuthStateSigner` — JWT-signs the OAuth state parameter (with optional PKCE verifier). |
| `exceptions.py` | `ProviderError` and friends — typed exceptions for the integration layer. |
| `authlib_factory.py` | `AuthlibClientFactory` — builds and caches one `AsyncOAuth2Client` per provider. |
| `adapter.py` | `OAuthAdapter` — runs the OAuth dance (authorize → exchange → refresh → revoke) for any provider. |
| `api_base.py` | `BaseApiClient` — parent class for `<provider>/client.py` files. |
| `token_resolver.py` | `TokenResolver` — fetches a credential from the DB and returns a plaintext access token. |
| `registry.py` | `ProviderCatalog` — explicit list of every registered provider. |

## Files inside `github/`

| File | Purpose |
|------|---------|
| `config.py` | `GITHUB` constant — the OAuthProviderConfig declaration. |
| `compliance.py` | Authlib hook that adds `Accept: application/json` to token requests. |
| `revoker.py` | Custom revocation flow (DELETE with Basic auth) — wired via `custom_revoker`. |
| `client.py` | `GitHubApiClient` — typed wrappers over GitHub REST endpoints. |
| `git_ops.py` | `GitHubGitOps` — gitpython-backed clone/push and URL parsing. |

## What does *not* belong here

- **Routes**: live in `src/api/views/`. They call `OAuthService` (in
  `src/services/`), which calls `OAuthAdapter`.
- **Persistence**: `UserOAuthCredential` model lives in `src/db/models/`,
  the repository in `src/db/queries/`. The integration layer only sees
  domain objects, not SQLAlchemy.
- **Encryption keys**: `Settings.fernet_key` plus `src/utils/crypto.py`.
  The integration layer never touches the cipher directly; `TokenResolver`
  is the single point of decryption.

## Testing a new provider locally

1. Register an OAuth app with the provider (their developer portal).
2. Set `<provider>_oauth_client_id` and `<provider>_oauth_client_secret`
   in `.env`.
3. Set the redirect URI in the provider's portal to
   `http://localhost:8000/api/v1/auth/oauth/<kind>/callback`.
4. Run the dev server (`make run-dev`).
5. Visit `/api/v1/auth/oauth/<kind>/start` while authenticated to start the
   flow. The provider will redirect back to your callback, the token will
   be encrypted and persisted, and you should see the credential in
   `user_oauth_credentials`.
