"""Unit tests for scripts.m2_pipeline.mapping (no DB) and parse helpers (no IO)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.hashing import (  # noqa: E402
    id_suffix_8,
    make_slug,
    product_id_from_url,
)
from scripts.m2_pipeline.mapping import (  # noqa: E402
    expected_spec_values_count,
    map_product,
)
from scripts.m2_pipeline.parse import (  # noqa: E402
    parse_row,
    parse_stock_quantity,
    parse_stock_status,
    parse_timestamp,
    parse_warranty_months,
    parse_price_vnd,
)


# --- parse_timestamp ---


def test_parse_timestamp_offset_normalized() -> None:
    dt = parse_timestamp("2026-06-15T10:21:38+0700")
    assert dt is not None
    assert dt.tzinfo is not None
    assert dt.utcoffset().total_seconds() == 7 * 3600
    # Convert to UTC
    assert dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S") == "2026-06-15 03:21:38"


def test_parse_timestamp_offset_with_colon() -> None:
    dt = parse_timestamp("2026-06-15T10:21:38+07:00")
    assert dt is not None
    assert dt.utcoffset().total_seconds() == 7 * 3600


def test_parse_timestamp_zulu() -> None:
    dt = parse_timestamp("2026-06-15T10:21:38Z")
    assert dt is not None
    assert dt.utcoffset().total_seconds() == 0


def test_parse_timestamp_naive_assumed_utc() -> None:
    dt = parse_timestamp("2026-06-15T10:21:38")
    assert dt is not None
    assert dt.tzinfo is timezone.utc


def test_parse_timestamp_invalid_returns_none() -> None:
    assert parse_timestamp("not a date") is None
    assert parse_timestamp("") is None
    assert parse_timestamp(None) is None


# --- parse_warranty_months ---


def test_warranty_months_prefers_normalized() -> None:
    # normalized_specs says 36, text says 12 tháng -> use 36
    assert parse_warranty_months("12 tháng", 36) == 36


def test_warranty_months_zero_is_valid() -> None:
    assert parse_warranty_months("không bảo hành", 0) == 0
    assert parse_warranty_months("0 tháng", None) == 0


def test_warranty_months_fallback_to_text() -> None:
    assert parse_warranty_months("Bảo hành 24 tháng", None) == 24
    assert parse_warranty_months("Bảo hành 1 tháng", None) == 1


def test_warranty_months_none() -> None:
    assert parse_warranty_months("no info", None) is None
    assert parse_warranty_months(None, None) is None


def test_warranty_months_ignores_text_when_normalized_invalid() -> None:
    # If normalized_specs.warranty_months is missing or invalid, fall back to text
    assert parse_warranty_months("12 tháng", None) == 12
    # 'abc' is not a number -> try text -> found 6
    assert parse_warranty_months("bảo hành 6 tháng", "abc") == 6


# --- parse_price_vnd ---


def test_parse_price_vnd_int() -> None:
    assert parse_price_vnd(20563000) == 20563000
    assert parse_price_vnd(0) == 0


def test_parse_price_vnd_string() -> None:
    assert parse_price_vnd("20,563,000") == 20563000
    assert parse_price_vnd("20.563.000") == 20563000
    assert parse_price_vnd(" 100 ") == 100


def test_parse_price_vnd_invalid() -> None:
    assert parse_price_vnd(None) is None
    assert parse_price_vnd("") is None
    assert parse_price_vnd("abc") is None


def test_parse_price_vnd_negative() -> None:
    # Negative is rejected (DB CHECK constraint)
    assert parse_price_vnd(-100) is None


# --- parse_stock_status ---


def test_parse_stock_status_known() -> None:
    assert parse_stock_status("in_stock") == "in_stock"
    assert parse_stock_status("OUT_OF_STOCK") == "out_of_stock"


def test_parse_stock_status_unknown_value_returned_as_is() -> None:
    # Unknown value passes through; DB CHECK will reject.
    assert parse_stock_status("pending_arrival") == "pending_arrival"


def test_parse_stock_status_empty() -> None:
    assert parse_stock_status("") is None
    assert parse_stock_status(None) is None


# --- parse_stock_quantity ---


def test_parse_stock_quantity() -> None:
    assert parse_stock_quantity(5) == 5
    assert parse_stock_quantity("10") == 10
    assert parse_stock_quantity(None) is None
    assert parse_stock_quantity("") is None


# --- parse_row ---


def test_parse_row_valid_minimal() -> None:
    raw = {
        "source": "anphatpc",
        "source_url": "https://anphatpc.com.vn/p/x.html",
        "name": "Laptop test",
        "category": "laptop",
    }
    parsed = parse_row(raw, row_index=1, default_source="anphatpc")
    assert parsed is not None
    assert parsed.source == "anphatpc"
    assert parsed.name == "Laptop test"
    assert parsed.images == []
    assert parsed.breadcrumbs == []


def test_parse_row_missing_required_returns_none() -> None:
    assert parse_row({"name": "x"}, 1, "anphatpc") is None
    assert parse_row({"source_url": "x", "name": "x"}, 1, "anphatpc") is None
    assert parse_row({"source_url": "x", "category": "laptop"}, 1, "anphatpc") is None


def test_parse_row_crawled_at_parsed() -> None:
    raw = {
        "source_url": "https://x.html",
        "name": "x",
        "category": "laptop",
        "crawled_at": "2026-06-15T10:21:38+0700",
    }
    p = parse_row(raw, 1, "anphatpc")
    assert p is not None
    assert p.crawled_at is not None
    assert p.crawled_at_from_source is True


def test_parse_row_invalid_timestamp_falls_back() -> None:
    raw = {
        "source_url": "https://x.html",
        "name": "x",
        "category": "laptop",
        "crawled_at": "garbage",
    }
    p = parse_row(raw, 1, "anphatpc")
    assert p is not None
    assert p.crawled_at is None
    assert p.crawled_at_from_source is False


# --- map_product: product_id / slug deterministic ---


SAMPLE_URL = "https://anphatpc.com.vn/p/sample-product.html"


def _build_sample_row(price_overrides: dict | None = None,
                      warranty_text="Bảo hành 36 tháng",
                      warranty_months=None,
                      normalized_specs=None,
                      ports=None,
                      description="Sample description",
                      stock_status="in_stock",
                      stock_quantity=5) -> dict:
    ns = {
        "product_type": "laptop",
        "model": "Sample-1",
        "cpu_model": "Intel Core i5-11400H",
        "cpu_cores": 6,
        "cpu_threads": 12,
        "ram_gb": 16,
        "ram_type": "DDR4",
        "storage_gb": 512,
        "storage_type": "SSD",
        "screen_inches": 15.6,
        "refresh_rate_hz": 144,
        "panel_type": "IPS",
        "os": "Windows 11",
        "ports": ports if ports is not None else ["USB-C", "HDMI", "USB-A"],
        "confidence": 0.9,
        "warnings": [],
    }
    if normalized_specs:
        ns.update(normalized_specs)
    if warranty_months is not None:
        ns["warranty_months"] = warranty_months
    p = {
        "list_price": 20000000,
        "sale_price": 18000000,
        "build_pc_price": None,
        "regional_price": None,
    }
    if price_overrides:
        p.update(price_overrides)
    return {
        "source": "anphatpc",
        "source_url": SAMPLE_URL,
        "name": "Laptop Sample",
        "category": "laptop",
        "subcategory": "gaming",
        "brand": "SampleBrand",
        "sku": "SKU-001",
        "source_product_id": "anphat-001",
        "thumbnail_url": "https://x/thumb.jpg",
        "images": ["https://x/1.jpg", "https://x/2.jpg"],
        "description": description,
        "breadcrumbs": ["Home", "Laptop", "Gaming"],
        "warranty": warranty_text,
        "raw_specs": {"CPU": "i5-11400H", "RAM": "16GB"},
        "validation_warnings": [],
        "llm_warnings": [],
        "prices": p,
        "stock": {"status": stock_status, "quantity": stock_quantity},
        "crawled_at": "2026-06-15T10:21:38+0700",
        "normalized_at": "2026-06-16T03:22:50+0700",
        "raw_html_path": "raw_html/foo.html",
        "normalized_specs": ns,
    }


def test_map_product_id_and_slug_match_hashing() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    assert parsed is not None
    m = map_product(parsed)

    expected_id = product_id_from_url("anphatpc", SAMPLE_URL)
    assert m.products_id == expected_id
    assert m.products_tuple[0] == expected_id  # id column

    expected_slug = make_slug("Laptop Sample", SAMPLE_URL)
    assert m.products_tuple[5] == expected_slug  # slug column


# --- map_product: 30/45 column counts ---


def test_map_product_tuple_widths() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)

    assert len(m.products_tuple) == 30
    assert len(m.product_specs_tuple) == 45
    assert len(m.chunks_rows) == 5
    assert m.prices_row is not None
    assert len(m.prices_row) == 11


# --- map_product: prices ---


def test_map_product_price_vnd_fallback_to_list() -> None:
    raw = _build_sample_row(price_overrides={"sale_price": None})
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    # sale=None, list=20_000_000 -> price_vnd = list
    assert m.products_tuple[12] == 20000000  # price_vnd
    assert m.prices_row["price_vnd"] == 20000000


def test_map_product_price_vnd_prefers_sale() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    # sale=18_000_000, list=20_000_000 -> price_vnd = sale
    assert m.products_tuple[12] == 18000000
    assert m.prices_row["price_vnd"] == 18000000


def test_map_product_all_4_prices_separate() -> None:
    raw = _build_sample_row(price_overrides={
        "list_price": 100,
        "sale_price": 90,
        "build_pc_price": 95,
        "regional_price": 92,
    })
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    assert m.products_tuple[13] == 100  # list
    assert m.products_tuple[14] == 90   # sale
    assert m.products_tuple[15] == 95   # build_pc
    assert m.products_tuple[16] == 92   # regional
    assert m.prices_row["list_price_vnd"] == 100
    assert m.prices_row["sale_price_vnd"] == 90
    assert m.prices_row["build_pc_price_vnd"] == 95
    assert m.prices_row["regional_price_vnd"] == 92


def test_map_product_price_hash_changes_with_sale() -> None:
    raw_a = _build_sample_row(price_overrides={"sale_price": 18000000})
    raw_b = _build_sample_row(price_overrides={"sale_price": 17000000})
    pa = parse_row(raw_a, 1, "anphatpc")
    pb = parse_row(raw_b, 1, "anphatpc")
    ma = map_product(pa)
    mb = map_product(pb)
    assert ma.prices_row["price_hash"] != mb.prices_row["price_hash"]


# --- map_product: chunks ---


def test_map_product_5_chunk_types() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    types = [row[1] for row in m.chunks_rows]
    assert types == ["title", "description", "specs", "raw_specs", "warranty"]
    # All chunk_index=0
    for row in m.chunks_rows:
        assert row[2] == 0


def test_map_product_title_chunk_format() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    title = m.chunks_rows[0][3]
    assert title == "Laptop Sample | brand=SampleBrand | category=laptop"


def test_map_product_specs_chunk_only_non_null() -> None:
    raw = _build_sample_row(normalized_specs={
        "product_type": "laptop",
        "model": "M-1",
        "cpu_model": "i5",  # only this one is set in addition to the base
        # base ns already has cpu_model, etc.
        "ram_gb": None,  # force null
    })
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    specs = m.chunks_rows[2][3]
    # Must contain non-null keys
    assert "cpu_model: i5" in specs
    # Null keys omitted
    assert "ram_gb:" not in specs


# --- map_product: spec_values (1:1 scalar, 1:N list) ---


def test_map_product_spec_values_scalars() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    # Find row with spec_key=cpu_model, spec_value='Intel Core i5-11400H'
    cpu_rows = [r for r in m.spec_values_rows if r[2] == "cpu_model"]
    assert len(cpu_rows) == 1
    assert cpu_rows[0][5] == "Intel Core i5-11400H"
    assert cpu_rows[0][4] == 0  # spec_index
    assert cpu_rows[0][3] == "cpu_model"  # normalized_key


def test_map_product_spec_values_list_with_index() -> None:
    raw = _build_sample_row(ports=["USB-A", "USB-C", "HDMI"])
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    port_rows = [r for r in m.spec_values_rows if r[2] == "ports"]
    assert len(port_rows) == 3
    for i, row in enumerate(port_rows):
        assert row[4] == i
        assert row[3] == f"ports[{i}]"
        assert row[1] == "ports"  # group_name


def test_map_product_spec_values_skips_warnings_confidence() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    keys = {r[2] for r in m.spec_values_rows}
    assert "warnings" not in keys
    assert "confidence" not in keys


def test_map_product_spec_values_unit_and_value_num() -> None:
    raw = _build_sample_row()
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    ram_rows = [r for r in m.spec_values_rows if r[2] == "ram_gb"]
    assert len(ram_rows) == 1
    assert ram_rows[0][6] == 16  # value_num
    assert ram_rows[0][7] == "gb"  # unit


# --- map_product: warranty_months ---


def test_map_product_warranty_months_prefers_normalized() -> None:
    raw = _build_sample_row(warranty_text="12 tháng", warranty_months=36)
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    assert m.products_tuple[20] == 36


def test_map_product_warranty_months_falls_back_to_text() -> None:
    raw = _build_sample_row(warranty_text="24 tháng", warranty_months=None)
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    assert m.products_tuple[20] == 24


# --- map_product: timestamp fallback ---


def test_map_product_captured_at_fallback() -> None:
    raw = _build_sample_row()
    raw["crawled_at"] = "garbage"
    parsed = parse_row(raw, 1, "anphatpc")
    m = map_product(parsed)
    # captured_at falls back to now() (UTC)
    cap = m.prices_row["captured_at"]
    assert cap is not None
    assert cap.tzinfo is timezone.utc


# --- expected_spec_values_count helper ---


def test_expected_spec_values_count_basic() -> None:
    rows = [
        {
            "normalized_specs": {
                "cpu_model": "i5",
                "ram_gb": 16,
                "ports": ["USB-A", "USB-C"],
                "warnings": ["x"],
                "confidence": 0.9,
            }
        },
        {
            "normalized_specs": {
                "model": "M",
                "weight_kg": 1.5,
            }
        },
        {"normalized_specs": None},
    ]
    # Row 0: cpu_model(1) + ram_gb(1) + ports(2) + skip warnings + skip confidence = 4
    # Row 1: model(1) + weight_kg(1) = 2
    # Row 2: no normalized_specs = 0
    assert expected_spec_values_count(rows) == 6
