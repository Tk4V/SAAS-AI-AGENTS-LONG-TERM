"""Schemas describing a single provider entry in the public catalog."""

from __future__ import annotations

import enum

from pydantic import BaseModel, ConfigDict, Field


class ProviderCategory(str, enum.Enum):
    """Top-level grouping used to render the catalog UI."""

    AI = "ai"
    PAYMENTS = "payments"
    DEV_TOOLS = "dev_tools"
    PRODUCTIVITY = "productivity"
    COMMUNICATION = "communication"
    PROJECT_MANAGEMENT = "project_management"
    IDENTITY = "identity"
    OTHER = "other"


class AuthMethodKind(str, enum.Enum):
    """Auth flows supported for a provider in this MVP scope."""

    OAUTH = "oauth"
    BEARER = "bearer"


class AuthMethod(BaseModel):
    """One auth path for a provider; a provider can support several."""

    model_config = ConfigDict(frozen=True)

    kind: AuthMethodKind
    # When ``kind=oauth`` and the provider is also wired in
    # ``ProviderCatalog`` (the OAuth machinery), this matches the
    # ``ProviderKind`` enum value used by the authorize/callback URLs.
    provider_id: str | None = None
    # Bearer-only fields. Help the UI show "Get your token" deep-links and
    # explain how the token should be sent on requests.
    token_creation_url: str | None = None
    token_format_hint: str | None = None
    header_name: str | None = None
    placement: str | None = None
    prefix: str | None = None


class ProviderCatalogEntry(BaseModel):
    """One provider as the frontend sees it."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    category: ProviderCategory
    icon: str | None = None
    api_base_url: str | None = None
    docs_url: str | None = None
    auth_methods: tuple[AuthMethod, ...] = Field(default_factory=tuple)
