"""Integration tests for M3 search and fallback (DB + Meili required).

Run with:
    uv run pytest -q tests/test_m3_search.py -m integration
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m2_pipeline.pipeline import PipelineOptions, run_pipeline  # noqa: E402
from scripts.m3_search.config import get_products_index  # noqa: E402
from scripts.m3_search.fallback import fallback_search  # noqa: E402
from scripts.m3_search.meili import (  # noqa: E402
    ensure_index,
    get_client,
)
from scripts.m3_search.search import FACETS_DEFAULT, search_products  # noqa: E402
from scripts.m3_search.sync import rebuild  # noqa: E402


pytestmark = [pytest.mark.integration, pytest.mark.requires_meili]


INDEX_NAME = get_products_index()


def _import_and_rebuild(clean_source: str, fixture_path: Path) -> int:
    fixture = fixture_path / "products_m3_search.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    result = run_pipeline(
        PipelineOptions(input_path=fixture, source=clean_source, dry_run=False)
    )
    assert result.status == "done", f"pipeline status={result.status}"
    res = rebuild(clean_source, INDEX_NAME, 1000)
    assert res["indexed"] == len([r for r in rows if r.get("name")])
    return res["indexed"]


def _hit_ids(res: dict) -> set[str]:
    return {h["id"] for h in res.get("hits", [])}


# --- search smoke ---------------------------------------------------------


def test_search_laptop_returns_laptop_rows(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "laptop", clean_source, limit=10)
    assert res["pagination"]["total_hits"] >= 1
    assert "fallback" in res
    assert res["fallback"] is False


def test_search_rtx_returns_gaming_pc(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "rtx", clean_source, limit=10)
    assert res["pagination"]["total_hits"] >= 1
    # Should match the desktop PC gaming row, not the mainboard/cpu rows.
    hit_names = {h["name"] for h in res["hits"]}
    assert any("RTX" in n or "rtx" in n.lower() for n in hit_names)


def test_search_typo_lapotp_finds_laptop(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "lapotp", clean_source, limit=10)
    assert res["pagination"]["total_hits"] >= 1


def test_search_diacritics_may_tinh_finds_vietnamese_row(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "may tinh", clean_source, limit=10)
    assert res["pagination"]["total_hits"] >= 1
    names = {h["name"] for h in res["hits"]}
    assert any("Máy tính" in n for n in names)


# --- filters --------------------------------------------------------------


def test_filter_category(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"category": "laptop"}, limit=50)
    assert res["pagination"]["total_hits"] >= 1
    assert all(h["category"] == "laptop" for h in res["hits"])


def test_filter_brand(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"brand": "ASUS"}, limit=50)
    assert res["pagination"]["total_hits"] >= 1
    assert all(h["brand"] == "ASUS" for h in res["hits"])


def test_filter_price_max(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"price_max": 5000000}, limit=50)
    for h in res["hits"]:
        assert h["price_vnd"] <= 5000000


def test_filter_ram_min(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"ram_min": 16}, limit=50)
    for h in res["hits"]:
        assert h["ram_gb"] is None or h["ram_gb"] >= 16


def test_filter_storage_min(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"storage_min": 256}, limit=50)
    for h in res["hits"]:
        assert h["storage_gb"] is None or h["storage_gb"] >= 256


def test_filter_refresh_rate_min(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, filters={"refresh_rate_min": 100}, limit=50)
    for h in res["hits"]:
        assert h["refresh_rate_hz"] is None or h["refresh_rate_hz"] >= 100


# --- sort -----------------------------------------------------------------


def test_sort_price_ascending(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, sort="price_asc", limit=10, facets=[])
    prices = [h["price_vnd"] for h in res["hits"] if h.get("price_vnd") is not None]
    assert prices == sorted(prices)


def test_sort_price_descending(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, sort="price_desc", limit=10, facets=[])
    prices = [h["price_vnd"] for h in res["hits"] if h.get("price_vnd") is not None]
    assert prices == sorted(prices, reverse=True)


# --- facets ---------------------------------------------------------------


def test_explicit_facets_returns_distributions(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "", clean_source, facets=FACETS_DEFAULT, limit=1)
    facets = res.get("facets") or {}
    for key in ("brand", "category", "ram_gb"):
        assert key in facets, f"missing facet {key}"


# --- edge cases -----------------------------------------------------------


def test_no_result_query_returns_empty(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    res = search_products(index, "zzzzz_no_match_query_xyz", clean_source, limit=10)
    assert res["pagination"]["total_hits"] == 0
    assert res["hits"] == []


# --- fallback -------------------------------------------------------------


def test_fallback_returns_distinct_products_only(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    res = fallback_search("laptop", clean_source, page=1, limit=20)
    assert res["fallback"] is True
    assert res["source"] == "postgres_fallback"
    ids = [h["id"] for h in res["hits"]]
    assert len(ids) == len(set(ids)), "fallback returned duplicate product ids"


def test_fallback_total_hits_is_distinct_product_count(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    res = fallback_search("laptop", clean_source, page=1, limit=20)
    # Sanity: total_hits matches len(hits) for a single page.
    assert res["pagination"]["total_hits"] == len(res["hits"])


def test_fallback_handles_no_result(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    res = fallback_search("zzzzz_no_match_query_xyz", clean_source, page=1, limit=20)
    assert res["pagination"]["total_hits"] == 0
    assert res["hits"] == []


def test_fallback_vietnamese_query(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    res = fallback_search("Máy tính", clean_source, page=1, limit=20)
    names = {h["name"] for h in res["hits"]}
    assert any("Máy tính" in n for n in names) or len(res["hits"]) == 0


# --- source isolation -----------------------------------------------------


def test_source_filter_isolates_results(clean_source, fixture_path):
    _import_and_rebuild(clean_source, fixture_path)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    # Search using a different source name; should return 0 hits.
    res = search_products(index, "laptop", "other_source_does_not_exist", limit=10)
    assert res["pagination"]["total_hits"] == 0
