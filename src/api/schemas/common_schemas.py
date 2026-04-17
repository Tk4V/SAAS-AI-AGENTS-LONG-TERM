"""Schemas shared across multiple resources.

Pagination uses page/page_size rather than cursor based navigation, since the
frontend currently shows numbered page controls. The error response shape is
declared so it appears in the generated OpenAPI spec; runtime errors are
serialised by `src.api.errors`.
"""

from __future__ import annotations

from typing import Annotated, Any, Generic, TypeVar

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class PaginationParams:
    """Reusable pagination query parameters.

    Used as a FastAPI dependency: `pagination: PaginationParams = Depends()`.
    """

    def __init__(
        self,
        page: Annotated[int, Query(ge=1, le=10_000, description="Page number, 1-based.")] = 1,
        page_size: Annotated[
            int,
            Query(ge=1, le=200, description="Number of items per page."),
        ] = 20,
    ) -> None:
        self.page = page
        self.page_size = page_size

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        return self.page_size


class Page(BaseModel, Generic[ItemT]):
    """Wrapper for a single page of results."""

    model_config = ConfigDict(from_attributes=True)

    items: list[ItemT]
    total: int = Field(description="Total number of items across all pages.")
    page: int
    page_size: int


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    """Schema of every non-2xx response produced by the API."""

    error: ErrorBody
