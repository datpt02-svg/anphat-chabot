"""Unit tests for M3 search filter + sort builders (no Meili required)."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m3_search.search import (  # noqa: E402
    SORT_MAP,
    build_filter,
    build_sort,
    normalize_limit,
    quote_filter_value,
)


# --- quote_filter_value ----------------------------------------------------


def test_quote_filter_value_simple():
    assert quote_filter_value("anphatpc") == '"anphatpc"'


def test_quote_filter_value_escapes_double_quote():
    out = quote_filter_value('a"b')
    assert out == '"a\\"b"'


def test_quote_filter_value_escapes_backslash():
    out = quote_filter_value("a\\b")
    assert out == '"a\\\\b"'


# --- build_filter: source always included ---------------------------------


def test_source_filter_always_included():
    out = build_filter(None, "anphatpc")
    assert out == 'source = "anphatpc"'


def test_source_filter_escapes_quote():
    out = build_filter(None, 'evil"src')
    assert 'source = "evil\\"src"' in out


# --- build_filter: string fields ------------------------------------------


def test_string_filter_category_brand():
    out = build_filter({"category": "laptop", "brand": "ASUS"}, "anphatpc")
    assert 'source = "anphatpc"' in out
    assert 'category = "laptop"' in out
    assert 'brand = "ASUS"' in out
    assert out.count(" AND ") == 2


def test_string_filter_skips_empty_values():
    out = build_filter({"category": "", "brand": "ASUS"}, "anphatpc")
    assert "category" not in out
    assert 'brand = "ASUS"' in out


def test_string_filter_escapes_user_value():
    out = build_filter({"brand": 'A"quote'}, "anphatpc")
    assert 'brand = "A\\"quote"' in out


# --- build_filter: numeric fields -----------------------------------------


def test_numeric_filter_price_range():
    out = build_filter({"price_min": 1000, "price_max": 5000}, "anphatpc")
    assert "price_vnd >= 1000" in out
    assert "price_vnd <= 5000" in out


def test_numeric_filter_accepts_float_string():
    out = build_filter({"price_min": "1000.0"}, "anphatpc")
    assert "price_vnd >= 1000" in out


def test_numeric_filter_rejects_non_numeric():
    with pytest.raises(ValueError, match="price_min must be numeric"):
        build_filter({"price_min": "abc"}, "anphatpc")


def test_numeric_filter_rejects_bool():
    with pytest.raises(ValueError, match="ram_min must be numeric"):
        build_filter({"ram_min": True}, "anphatpc")


def test_numeric_filter_rejects_infinity():
    with pytest.raises(ValueError, match="ram_min must be finite"):
        build_filter({"ram_min": float("inf")}, "anphatpc")


# --- build_filter: whitelist ----------------------------------------------


def test_unknown_filter_rejected():
    with pytest.raises(ValueError, match="Unknown filter"):
        build_filter({"hacker": "x"}, "anphatpc")


def test_unknown_filter_lists_all_unknown():
    with pytest.raises(ValueError) as exc:
        build_filter({"foo": 1, "bar": 2, "category": "laptop"}, "anphatpc")
    msg = str(exc.value)
    assert "bar" in msg
    assert "foo" in msg
    assert "category" not in msg


# --- build_sort ------------------------------------------------------------


def test_sort_relevance_returns_none():
    assert build_sort("relevance") is None
    assert build_sort(None) is None


def test_sort_known_keys():
    assert build_sort("price_asc") == ["price_vnd:asc"]
    assert build_sort("price_desc") == ["price_vnd:desc"]
    assert build_sort("newest") == ["updated_at:desc"]
    assert build_sort("name_asc") == ["name:asc"]
    assert build_sort("ram_desc") == ["ram_gb:desc"]
    assert build_sort("storage_desc") == ["storage_gb:desc"]
    assert build_sort("refresh_rate_desc") == ["refresh_rate_hz:desc"]


def test_sort_unknown_rejected():
    with pytest.raises(ValueError, match="Unknown sort"):
        build_sort("hacker")


def test_sort_map_contains_locked_keys():
    expected = {
        "relevance", "price_asc", "price_desc", "newest", "name_asc",
        "ram_desc", "storage_desc", "refresh_rate_desc",
    }
    assert set(SORT_MAP) == expected


# --- normalize_limit -------------------------------------------------------


def test_normalize_limit_default():
    assert normalize_limit(None) == 24


def test_normalize_limit_clamps_to_max():
    assert normalize_limit(10000) == 100


def test_normalize_limit_keeps_within_max():
    assert normalize_limit(50) == 50


def test_normalize_limit_rejects_zero_or_negative():
    with pytest.raises(ValueError, match="limit must be > 0"):
        normalize_limit(0)
    with pytest.raises(ValueError, match="limit must be > 0"):
        normalize_limit(-1)
