"""Meilisearch settings for the products index."""
from __future__ import annotations

from typing import Any

SEARCHABLE_ATTRIBUTES = [
    "name",
    "brand",
    "sku",
    "category",
    "subcategory",
    "breadcrumbs",
    "model",
    "cpu_model",
    "gpu_model",
    "spec_summary",
    "normalized_tokens",
    "warranty_text",
]

FILTERABLE_ATTRIBUTES = [
    "id",
    "source",
    "brand",
    "category",
    "subcategory",
    "stock_status",
    "price_vnd",
    "list_price_vnd",
    "sale_price_vnd",
    "warranty_months",
    "product_type",
    "cpu_model",
    "socket",
    "ram_gb",
    "ram_type",
    "ram_speed_mhz",
    "storage_gb",
    "storage_type",
    "gpu_model",
    "gpu_vram_gb",
    "screen_inches",
    "refresh_rate_hz",
    "panel_type",
    "form_factor",
    "psu_wattage_w",
    "recommended_psu_w",
]

SORTABLE_ATTRIBUTES = [
    "price_vnd",
    "list_price_vnd",
    "sale_price_vnd",
    "updated_at",
    "name",
    "warranty_months",
    "ram_gb",
    "storage_gb",
    "refresh_rate_hz",
]

DISPLAYED_ATTRIBUTES = [
    "id",
    "slug",
    "source",
    "source_url",
    "sku",
    "name",
    "brand",
    "category",
    "subcategory",
    "breadcrumbs",
    "thumbnail_url",
    "price_vnd",
    "list_price_vnd",
    "sale_price_vnd",
    "stock_status",
    "stock_quantity",
    "warranty_months",
    "warranty_text",
    "product_type",
    "model",
    "cpu_model",
    "socket",
    "ram_gb",
    "ram_type",
    "storage_gb",
    "storage_type",
    "gpu_model",
    "screen_inches",
    "refresh_rate_hz",
    "spec_summary",
    "normalized_tokens",
    "updated_at",
]

RANKING_RULES = [
    "words",
    "typo",
    "proximity",
    "attribute",
    "sort",
    "exactness",
]

DESIRED_SETTINGS = {
    "searchableAttributes": SEARCHABLE_ATTRIBUTES,
    "filterableAttributes": FILTERABLE_ATTRIBUTES,
    "sortableAttributes": SORTABLE_ATTRIBUTES,
    "displayedAttributes": DISPLAYED_ATTRIBUTES,
    "rankingRules": RANKING_RULES,
}


def _get(obj: Any, key: str, default=None):
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_settings(settings: Any) -> dict:
    return {
        "searchableAttributes": list(_get(settings, "searchableAttributes", []) or []),
        "filterableAttributes": list(_get(settings, "filterableAttributes", []) or []),
        "sortableAttributes": list(_get(settings, "sortableAttributes", []) or []),
        "displayedAttributes": list(_get(settings, "displayedAttributes", []) or []),
        "rankingRules": list(_get(settings, "rankingRules", []) or []),
    }


# Settings where the order of entries carries semantic meaning in Meilisearch
# (e.g. `searchableAttributes` is used for ranking priority, `rankingRules`
# is the actual ranking pipeline). We compare these as ordered lists.
#
# Settings where the order is irrelevant to Meili behavior — Meili may return
# the entries in a different order than the one we sent. We compare as sets.
_ORDERED_SETTINGS = frozenset({"searchableAttributes", "rankingRules"})


def settings_match(settings: Any) -> bool:
    current = normalize_settings(settings)
    for key, desired_value in DESIRED_SETTINGS.items():
        current_value = current.get(key) or []
        if key in _ORDERED_SETTINGS:
            if list(current_value) != list(desired_value):
                return False
        else:
            if set(current_value) != set(desired_value):
                return False
    return True


def read_settings(index) -> dict:
    settings = index.get_settings()
    return normalize_settings(settings)


def apply_settings(index, wait_for_task) -> dict:
    current = index.get_settings()
    if settings_match(current):
        return {"status": "skipped", "reason": "settings_unchanged"}
    task = index.update_settings(DESIRED_SETTINGS)
    wait_for_task(task)
    return {"status": "updated"}
