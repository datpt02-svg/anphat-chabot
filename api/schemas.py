"""Pydantic models and custom exceptions for M4 catalog API."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    error: str
    code: str
    details: dict[str, Any] = Field(default_factory=dict)


class SpecItem(BaseModel):
    label: str
    value: str | None = None


class CurrentPrice(BaseModel):
    price_vnd: int | None = None
    list_price_vnd: int | None = None
    stock_status: str | None = None
    captured_at: str | None = None


class SpecsSummary(BaseModel):
    cpu_model: str | None = None
    ram_gb: int | None = None
    storage_gb: int | None = None
    gpu_model: str | None = None
    model_config = {"extra": "allow"}


class Pagination(BaseModel):
    page: int
    limit: int
    total_hits: int
    total_pages: int


class SearchHit(BaseModel):
    id: str
    slug: str | None = None
    name: str
    brand: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None
    price_vnd: int | None = None
    list_price_vnd: int | None = None
    sale_price_vnd: int | None = None
    stock_status: str | None = None
    spec_summary: str | None = None


SearchSource = Literal["meilisearch", "postgres"]


class SearchResponse(BaseModel):
    query: str
    source: SearchSource
    fallback: bool
    hits: list[SearchHit]
    facets: dict[str, dict[str, int]] = Field(default_factory=dict)
    pagination: Pagination
    processing_time_ms: int | None = None


SortKey = Literal["relevance", "price_asc", "price_desc", "newest", "name_asc"]


class ProductDetail(BaseModel):
    id: str
    slug: str
    source: str
    source_url: str | None = None
    sku: str | None = None
    name: str
    brand: str | None = None
    category: str
    breadcrumbs: list[str] = Field(default_factory=list)
    images: list[str] = Field(default_factory=list)
    description: str | None = None
    current_price: CurrentPrice | None = None
    specs_summary: SpecsSummary | None = None
    specs_grouped: dict[str, list[SpecItem]] = Field(default_factory=dict)
    updated_at: str | None = None


class RelatedProduct(BaseModel):
    id: str
    slug: str
    name: str
    brand: str | None = None
    category: str | None = None
    thumbnail_url: str | None = None
    price_vnd: int | None = None
    list_price_vnd: int | None = None
    stock_status: str | None = None


class HealthStatus(BaseModel):
    status: Literal["ok", "degraded"]
    postgres: bool
    meilisearch: bool


class APIError(Exception):
    code: str = "INTERNAL_ERROR"
    status_code: int = 500

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class ProductNotFound(APIError):
    code = "PRODUCT_NOT_FOUND"
    status_code = 404
