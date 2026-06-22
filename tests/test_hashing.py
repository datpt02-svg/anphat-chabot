"""Unit tests for scripts.m2_pipeline.hashing. No DB required."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.hashing import (  # noqa: E402
    canonical_json,
    coerce_number,
    derive_group,
    extract_unit,
    id_suffix_8,
    make_slug,
    payload_hash,
    price_hash_components,
    product_id_from_url,
    sha256_hex,
    slugify,
)


# --- product_id_from_url ---


def test_product_id_from_url_deterministic() -> None:
    url = "https://anphatpc.com.vn/p/laptop-abc.html"
    a = product_id_from_url("anphatpc", url)
    b = product_id_from_url("anphatpc", url)
    assert a == b
    assert a.startswith("anphatpc:")
    assert len(a.split(":")[1]) == 16


def test_product_id_from_url_differs_per_url() -> None:
    a = product_id_from_url("anphatpc", "https://anphatpc.com.vn/p/a.html")
    b = product_id_from_url("anphatpc", "https://anphatpc.com.vn/p/b.html")
    assert a != b


def test_product_id_from_url_differs_per_source() -> None:
    url = "https://anphatpc.com.vn/p/a.html"
    a = product_id_from_url("anphatpc", url)
    b = product_id_from_url("other", url)
    assert a != b


# --- slugify ---


def test_slugify_basic_ascii() -> None:
    assert slugify("Laptop Gaming Asus") == "laptop-gaming-asus"


def test_slugify_strip_accents() -> None:
    assert slugify("Máy tính") == "may-tinh"
    assert slugify("Đặc biệt") == "dac-biet"
    assert slugify("Bàn phím cơ") == "ban-phim-co"


def test_slugify_collapse_dashes() -> None:
    assert slugify("abc---xyz") == "abc-xyz"
    assert slugify("  hello   world  ") == "hello-world"


def test_slugify_empty() -> None:
    assert slugify("") == ""
    assert slugify("   ") == ""
    assert slugify("---") == ""


def test_slugify_max_length() -> None:
    text = "a" * 200
    out = slugify(text, max_length=50)
    assert len(out) == 50
    assert out == "a" * 50


def test_slugify_max_length_trims_dash() -> None:
    text = "a" * 49 + "-b"
    out = slugify(text, max_length=50)
    assert not out.endswith("-")


# --- id_suffix_8 + make_slug ---


def test_id_suffix_8_length() -> None:
    assert len(id_suffix_8("https://example.com")) == 8


def test_make_slug_with_suffix() -> None:
    s = make_slug("Laptop HP", "https://anphatpc.com.vn/p/laptop-hp.html")
    # Format: <slug>-<8 hex>
    parts = s.rsplit("-", 1)
    assert len(parts) == 2
    assert parts[0] == "laptop-hp"
    assert len(parts[1]) == 8
    assert parts[1].isalnum() and all(c in "0123456789abcdef" for c in parts[1])


def test_make_slug_collision_guard() -> None:
    # Same name, different URL => different slug (8-hex suffix differs)
    a = make_slug("Laptop HP", "https://anphatpc.com.vn/p/a.html")
    b = make_slug("Laptop HP", "https://anphatpc.com.vn/p/b.html")
    assert a != b


# --- canonical_json ---


def test_canonical_json_stable_key_order() -> None:
    a = canonical_json({"b": 2, "a": 1, "c": 3})
    b = canonical_json({"c": 3, "a": 1, "b": 2})
    assert a == b
    assert a == '{"a":1,"b":2,"c":3}'


def test_canonical_json_whitespace_independent() -> None:
    a = canonical_json({"a": 1, "b": [1, 2, 3]})
    # The function uses fixed separators; no whitespace in output
    assert " " not in a
    assert "\n" not in a


def test_canonical_json_unicode_preserved() -> None:
    a = canonical_json({"name": "Đặc biệt"})
    assert "Đặc biệt" in a  # ensure_ascii=False


# --- sha256_hex + payload_hash ---


def test_sha256_hex_known_value() -> None:
    # Known sha256 of 'abc' is ba7816bf8...
    assert sha256_hex("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


def test_payload_hash_stable() -> None:
    a = payload_hash({"a": 1, "b": 2})
    b = payload_hash({"b": 2, "a": 1})
    assert a == b


# --- price_hash ---


def test_price_hash_stable_same_input() -> None:
    h1 = price_hash_components(100, 200, 150, None, None, "in_stock", 5)
    h2 = price_hash_components(100, 200, 150, None, None, "in_stock", 5)
    assert h1 == h2


def test_price_hash_changes_on_sale_price() -> None:
    h1 = price_hash_components(100, 200, 150, None, None, "in_stock", 5)
    h2 = price_hash_components(100, 200, 151, None, None, "in_stock", 5)
    assert h1 != h2


def test_price_hash_changes_on_stock() -> None:
    h1 = price_hash_components(100, 200, 150, None, None, "in_stock", 5)
    h2 = price_hash_components(100, 200, 150, None, None, "out_of_stock", 5)
    assert h1 != h2


def test_price_hash_handles_null() -> None:
    h1 = price_hash_components(None, None, None, None, None, None, None)
    h2 = price_hash_components(None, None, None, None, None, None, None)
    assert h1 == h2


# --- extract_unit ---


def test_extract_unit_keys() -> None:
    assert extract_unit("ram_gb") == "gb"
    assert extract_unit("cpu_base_clock_ghz") == "ghz"
    assert extract_unit("screen_inches") == "inch"
    assert extract_unit("weight_kg") == "kg"
    assert extract_unit("refresh_rate_hz") == "hz"
    assert extract_unit("psu_wattage_w") == "w"
    assert extract_unit("ports") is None
    assert extract_unit("model") is None
    assert extract_unit("os") is None


def test_extract_unit_mhz_not_hz() -> None:
    # 'mhz' is longer than 'hz' and comes first; ensure no partial match.
    assert extract_unit("ram_speed_mhz") == "mhz"
    assert extract_unit("ram_speed_ghz") == "ghz"


# --- derive_group ---


def test_derive_group_known() -> None:
    assert derive_group("cpu_model") == "cpu"
    assert derive_group("ram_gb") == "ram"
    assert derive_group("storage_gb") == "storage"
    assert derive_group("gpu_model") == "gpu"
    assert derive_group("screen_inches") == "screen"
    assert derive_group("ports") == "ports"
    assert derive_group("os") == "os"
    assert derive_group("warranty_months") == "warranty"


def test_derive_group_fallback() -> None:
    assert derive_group("brand") == "other"
    assert derive_group("category") == "other"
    assert derive_group("unknown_key") == "other"


# --- coerce_number ---


def test_coerce_number() -> None:
    assert coerce_number(5) == 5
    assert coerce_number(5.5) == 5.5
    assert coerce_number("10") == 10
    assert coerce_number("3.14") == 3.14
    assert coerce_number("1,200") == 1200
    assert coerce_number(None) is None
    assert coerce_number("") is None
    assert coerce_number("abc") is None
    assert coerce_number(True) is None  # bool not numeric
