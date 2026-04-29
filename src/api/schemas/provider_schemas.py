"""Pydantic schemas for the providers catalog endpoint."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from src.credentials.catalog.models import (
    AuthMethod,
    AuthMethodKind,
    ProviderCatalogEntry,
    ProviderCategory,
)


class AuthMethodRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: AuthMethodKind
    provider_id: str | None = None
    token_creation_url: str | None = None
    token_format_hint: str | None = None
    header_name: str | None = None
    placement: str | None = None
    prefix: str | None = None

    @classmethod
    def from_entry(cls, method: AuthMethod) -> AuthMethodRead:
        return cls(
            kind=method.kind,
            provider_id=method.provider_id,
            token_creation_url=method.token_creation_url,
            token_format_hint=method.token_format_hint,
            header_name=method.header_name,
            placement=method.placement,
            prefix=method.prefix,
        )


class ProviderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    category: ProviderCategory
    icon: str | None = None
    api_base_url: str | None = None
    docs_url: str | None = None
    auth_methods: list[AuthMethodRead]

    @classmethod
    def from_entry(cls, entry: ProviderCatalogEntry) -> ProviderRead:
        return cls(
            id=entry.id,
            name=entry.name,
            category=entry.category,
            icon=entry.icon,
            api_base_url=entry.api_base_url,
            docs_url=entry.docs_url,
            auth_methods=[AuthMethodRead.from_entry(m) for m in entry.auth_methods],
        )


class ProvidersList(BaseModel):
    items: list[ProviderRead]
