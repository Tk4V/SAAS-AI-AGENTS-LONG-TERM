"""Public catalog of providers for the integrations UI."""

from __future__ import annotations

from fastapi import APIRouter

from src.api.dependencies import PublicProviderCatalogDep
from src.api.schemas.provider_schemas import ProviderRead, ProvidersList
from src.utils.exceptions import NotFoundError

router = APIRouter(prefix="/providers", tags=["providers"])


class ProvidersView:
    """Read-only listing of providers the user can connect."""

    @staticmethod
    @router.get("", response_model=ProvidersList)
    async def list(catalog: PublicProviderCatalogDep) -> ProvidersList:
        """Return every known provider, ordered for default UI rendering."""
        return ProvidersList(
            items=[ProviderRead.from_entry(entry) for entry in catalog.all()]
        )

    @staticmethod
    @router.get("/{provider_id}", response_model=ProviderRead)
    async def get(
        provider_id: str,
        catalog: PublicProviderCatalogDep,
    ) -> ProviderRead:
        """Return one provider by id."""
        entry = catalog.get(provider_id)
        if entry is None:
            raise NotFoundError(f"Provider {provider_id!r} is not in the catalog.")
        return ProviderRead.from_entry(entry)
