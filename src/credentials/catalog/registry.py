"""Static catalog of providers exposed to the frontend.

Adding a provider means appending one entry to ``PROVIDER_CATALOG``. No
migrations, no DB writes. The list is stable and ordered by usefulness so
the UI can render "popular" tiles without a separate ranking field.

Providers with ``AuthMethodKind.OAUTH`` and a ``provider_id`` set should
also be registered in ``src.integrations._shared.ProviderCatalog`` so the
``/credentials/oauth/{provider}/authorize`` endpoint actually works.
"""

from __future__ import annotations

from functools import lru_cache

from src.credentials.catalog.models import (
    AuthMethod,
    AuthMethodKind,
    ProviderCatalogEntry,
    ProviderCategory,
)

PROVIDER_CATALOG: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        id="github",
        name="GitHub",
        category=ProviderCategory.DEV_TOOLS,
        api_base_url="https://api.github.com",
        docs_url="https://docs.github.com/en/rest",
        auth_methods=(
            AuthMethod(kind=AuthMethodKind.OAUTH, provider_id="github"),
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://github.com/settings/tokens?type=beta",
                token_format_hint="ghp_*** or github_pat_***",
                header_name="Authorization",
                placement="header",
                prefix="Bearer ",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="google",
        name="Google",
        category=ProviderCategory.IDENTITY,
        docs_url="https://developers.google.com/identity/protocols/oauth2",
        auth_methods=(
            AuthMethod(kind=AuthMethodKind.OAUTH, provider_id="google"),
        ),
    ),
    ProviderCatalogEntry(
        id="slack",
        name="Slack",
        category=ProviderCategory.COMMUNICATION,
        api_base_url="https://slack.com/api",
        docs_url="https://api.slack.com/web",
        auth_methods=(
            AuthMethod(kind=AuthMethodKind.OAUTH, provider_id="slack"),
        ),
    ),
    ProviderCatalogEntry(
        id="jira",
        name="Jira",
        category=ProviderCategory.PROJECT_MANAGEMENT,
        api_base_url="https://api.atlassian.com",
        docs_url="https://developer.atlassian.com/cloud/jira/platform/rest/v3/intro/",
        auth_methods=(
            AuthMethod(kind=AuthMethodKind.OAUTH, provider_id="jira"),
        ),
    ),
    ProviderCatalogEntry(
        id="linear",
        name="Linear",
        category=ProviderCategory.PROJECT_MANAGEMENT,
        api_base_url="https://api.linear.app",
        docs_url="https://developers.linear.app/docs",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://linear.app/settings/api",
                token_format_hint="lin_api_***",
                header_name="Authorization",
                placement="header",
                prefix="",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="stripe",
        name="Stripe",
        category=ProviderCategory.PAYMENTS,
        api_base_url="https://api.stripe.com",
        docs_url="https://docs.stripe.com/api",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://dashboard.stripe.com/apikeys",
                token_format_hint="sk_live_*** or rk_live_***",
                header_name="Authorization",
                placement="header",
                prefix="Bearer ",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="openai",
        name="OpenAI",
        category=ProviderCategory.AI,
        api_base_url="https://api.openai.com",
        docs_url="https://platform.openai.com/docs/api-reference",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://platform.openai.com/api-keys",
                token_format_hint="sk-***",
                header_name="Authorization",
                placement="header",
                prefix="Bearer ",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="anthropic",
        name="Anthropic",
        category=ProviderCategory.AI,
        api_base_url="https://api.anthropic.com",
        docs_url="https://docs.anthropic.com/en/api",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://console.anthropic.com/settings/keys",
                token_format_hint="sk-ant-***",
                header_name="x-api-key",
                placement="header",
                prefix="",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="notion",
        name="Notion",
        category=ProviderCategory.PRODUCTIVITY,
        api_base_url="https://api.notion.com",
        docs_url="https://developers.notion.com/reference/intro",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://www.notion.so/my-integrations",
                token_format_hint="secret_***",
                header_name="Authorization",
                placement="header",
                prefix="Bearer ",
            ),
        ),
    ),
    ProviderCatalogEntry(
        id="sentry",
        name="Sentry",
        category=ProviderCategory.DEV_TOOLS,
        api_base_url="https://sentry.io/api",
        docs_url="https://docs.sentry.io/api/",
        auth_methods=(
            AuthMethod(
                kind=AuthMethodKind.BEARER,
                token_creation_url="https://sentry.io/settings/account/api/auth-tokens/",
                token_format_hint="sntrys_***",
                header_name="Authorization",
                placement="header",
                prefix="Bearer ",
            ),
        ),
    ),
)


class PublicProviderCatalog:
    """Read-only lookup over the static provider list."""

    def __init__(
        self, entries: tuple[ProviderCatalogEntry, ...] = PROVIDER_CATALOG
    ) -> None:
        self._entries = entries
        self._by_id: dict[str, ProviderCatalogEntry] = {e.id: e for e in entries}

    def all(self) -> tuple[ProviderCatalogEntry, ...]:
        return self._entries

    def by_category(
        self, category: ProviderCategory
    ) -> tuple[ProviderCatalogEntry, ...]:
        return tuple(e for e in self._entries if e.category is category)

    def get(self, provider_id: str) -> ProviderCatalogEntry | None:
        return self._by_id.get(provider_id)


@lru_cache(maxsize=1)
def get_public_provider_catalog() -> PublicProviderCatalog:
    return PublicProviderCatalog()
