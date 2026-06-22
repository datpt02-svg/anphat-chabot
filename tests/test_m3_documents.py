"""Unit tests for M3 document builder (no DB, no Meili required)."""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m3_search.documents import (  # noqa: E402
    build_document,
    build_normalized_tokens,
    build_spec_summary,
    normalize_breadcrumbs,
)


def _row(**overrides):
    base = {
        "id": "anphatpc:abc1234567",
        "source": "anphatpc",
        "source_url": "https://www.anphatpc.com.vn/p/test.html",
        "source_product_id": "abc",
        "sku": "SKU-001",
        "slug": "test-product-abc12345",
        "name": "Laptop ASUS Test",
        "brand": "ASUS",
        "category": "laptop",
        "subcategory": "laptop-gaming",
        "thumbnail_url": "https://example.com/thumb.jpg",
        "images": ["https://example.com/1.jpg"],
        "price_vnd": 15000000,
        "list_price_vnd": 17000000,
        "sale_price_vnd": 15000000,
        "build_pc_price_vnd": None,
        "regional_price_vnd": None,
        "stock_status": "in_stock",
        "stock_quantity": 5,
        "warranty_text": "Bảo hành 24 tháng",
        "warranty_months": 24,
        "description": "desc",
        "breadcrumbs": ["Laptop", "Laptop ASUS"],
        "status": "active",
        "updated_at": datetime(2026, 6, 22, 10, 0, tzinfo=timezone.utc),
        "product_type": "laptop",
        "model": "Vivobook X",
        "cpu_model": "Intel Core i5-13420H",
        "socket": None,
        "ram_gb": 16,
        "ram_type": "DDR4",
        "ram_speed_mhz": 3200,
        "storage_gb": 512,
        "storage_type": "SSD",
        "gpu_model": "RTX 4050",
        "gpu_vram_gb": 6,
        "screen_inches": 15.6,
        "resolution_label": "FHD",
        "refresh_rate_hz": 144,
        "panel_type": "IPS",
        "form_factor": None,
        "psu_wattage_w": None,
        "recommended_psu_w": None,
        "current_price_vnd": None,
        "current_list_price_vnd": None,
        "current_sale_price_vnd": None,
        "current_stock_status": None,
    }
    base.update(overrides)
    return base


# --- build_document: required fields ---------------------------------------


def test_build_document_includes_required_fields():
    doc = build_document(_row())
    for key in (
        "id", "product_id", "slug", "source", "source_url", "sku", "name", "brand",
        "category", "subcategory", "breadcrumbs", "thumbnail_url",
        "price_vnd", "stock_status", "stock_quantity", "warranty_months",
        "product_type", "model", "cpu_model", "ram_gb", "storage_gb",
        "screen_inches", "spec_summary", "normalized_tokens", "updated_at",
    ):
        assert key in doc, f"missing {key}"


def test_build_document_id_is_meili_safe():
    # products.id is `{source}:{hash}` (colon). Meili document IDs only allow
    # `[a-zA-Z0-9_-]+`, so we sanitize colon -> underscore.
    doc = build_document(_row(id="anphatpc:abc1234567"))
    assert doc["id"] == "anphatpc_abc1234567"
    # original id is preserved on the `product_id` field for roundtrip
    assert doc["product_id"] == "anphatpc:abc1234567"
    assert ":" not in doc["id"]


def test_build_document_no_raw_specs_no_description_no_search_text():
    doc = build_document(_row())
    assert "raw_specs" not in doc
    assert "description" not in doc
    assert "search_text" not in doc


# --- price priority --------------------------------------------------------


def test_build_document_price_prefers_current_prices():
    doc = build_document(_row(
        price_vnd=1000,
        current_price_vnd=2222,
        list_price_vnd=3000,
        current_list_price_vnd=3333,
        sale_price_vnd=1500,
        current_sale_price_vnd=1555,
    ))
    assert doc["price_vnd"] == 2222
    assert doc["list_price_vnd"] == 3333
    assert doc["sale_price_vnd"] == 1555


def test_build_document_price_falls_back_to_base_when_current_null():
    doc = build_document(_row(price_vnd=1111, current_price_vnd=None))
    assert doc["price_vnd"] == 1111


def test_build_document_stock_status_prefers_current():
    doc = build_document(_row(
        stock_status="in_stock",
        current_stock_status="out_of_stock",
    ))
    assert doc["stock_status"] == "out_of_stock"


# --- numeric / null discipline ---------------------------------------------


def test_numeric_fields_stay_numeric_or_null():
    doc = build_document(_row(ram_gb=None, storage_gb=None, screen_inches=None, refresh_rate_hz=None))
    assert doc["ram_gb"] is None
    assert doc["storage_gb"] is None
    assert doc["screen_inches"] is None
    assert doc["refresh_rate_hz"] is None
    assert doc["ram_speed_mhz"] == 3200
    assert isinstance(doc["ram_gb"], type(None))


def test_missing_thumbnail_url_stays_null():
    doc = build_document(_row(thumbnail_url=None))
    assert doc["thumbnail_url"] is None


# --- spec_summary ----------------------------------------------------------


def test_spec_summary_skips_nulls():
    s = build_spec_summary(_row(
        cpu_model="Intel Core i5-13420H",
        ram_gb=16, ram_type="DDR4",
        storage_gb=512, storage_type="SSD",
        gpu_model=None, screen_inches=15.6, refresh_rate_hz=144,
    ))
    assert "i5-13420H" in s
    assert "16GB DDR4" in s
    assert "SSD 512GB" in s
    assert "RTX" not in s
    assert "inch" in s


def test_spec_summary_handles_missing_storage_type():
    s = build_spec_summary(_row(storage_gb=256, storage_type=None))
    assert "Storage 256GB" in s


# --- normalized_tokens -----------------------------------------------------


def test_normalized_tokens_i5_dash_and_space_and_compact():
    toks = build_normalized_tokens(_row(cpu_model="Intel Core i5-13420H"))
    assert "i5-13420h" in toks
    assert "i5 13420h" in toks
    assert "i513420h" in toks


def test_normalized_tokens_ryzen_short_form():
    toks = build_normalized_tokens(_row(cpu_model="AMD Ryzen 7 7700X"))
    assert "ryzen 7 7700x" in toks
    assert "r7 7700x" in toks


def test_normalized_tokens_gpu_rtx_spaced_and_compact():
    toks = build_normalized_tokens(_row(gpu_model="NVIDIA RTX 4050"))
    assert "rtx 4050" in toks
    assert "rtx4050" in toks


def test_normalized_tokens_gpu_rtx_ti():
    toks = build_normalized_tokens(_row(gpu_model="RTX 4070 Ti"))
    assert "rtx 4070 ti" in toks
    assert "rtx4070ti" in toks
    assert "4070 ti" in toks


def test_normalized_tokens_ram_gb_and_combined_type():
    toks = build_normalized_tokens(_row(ram_gb=16, ram_type="DDR4"))
    assert "16gb" in toks
    assert "16 gb" in toks
    assert "ddr4" in toks
    assert "16gb ddr4" in toks


def test_normalized_tokens_storage_ssd_compact():
    toks = build_normalized_tokens(_row(storage_gb=512, storage_type="SSD"))
    assert "512gb" in toks
    assert "ssd512" in toks


def test_normalized_tokens_storage_tb_conversion():
    toks = build_normalized_tokens(_row(storage_gb=1024, storage_type="SSD"))
    assert "1tb" in toks


def test_normalized_tokens_screen_inches():
    toks = build_normalized_tokens(_row(screen_inches=15.6))
    assert any(t.startswith("15.6") for t in toks)


def test_normalized_tokens_refresh_rate():
    toks = build_normalized_tokens(_row(refresh_rate_hz=144))
    assert "144hz" in toks
    assert "144 hz" in toks


def test_normalized_tokens_dedup():
    toks = build_normalized_tokens(_row(cpu_model="Intel Core i5-13420H", ram_gb=16))
    assert len(toks) == len(set(toks))


def test_normalized_tokens_vietnamese_unaccent_added():
    toks = build_normalized_tokens(_row(name="Máy tính Đặc biệt", category="Laptop"))
    assert "may tinh dac biet" in toks


def test_normalized_tokens_no_false_positive_unaccent():
    # Already-ASCII names should NOT be re-added in lower form (they are
    # already covered by searchable_attributes on `name`/`category`/...).
    toks = build_normalized_tokens(_row(name="Laptop", category="laptop"))
    assert "laptop" not in toks


def test_normalized_tokens_handles_nulls():
    toks = build_normalized_tokens(_row(
        cpu_model=None, gpu_model=None, ram_gb=None, storage_gb=None,
        screen_inches=None, refresh_rate_hz=None,
    ))
    assert toks == []


# --- breadcrumbs -----------------------------------------------------------


def test_breadcrumbs_string_array_unchanged():
    assert normalize_breadcrumbs(["Laptop", "ASUS"]) == ["Laptop", "ASUS"]


def test_breadcrumbs_empty_input():
    assert normalize_breadcrumbs([]) == []
    assert normalize_breadcrumbs(None) == []


def test_breadcrumbs_object_array_uses_name_field():
    out = normalize_breadcrumbs([{"name": "Laptop"}, {"name": "ASUS"}])
    assert out == ["Laptop", "ASUS"]


def test_breadcrumbs_object_array_falls_back_to_label_and_title():
    out = normalize_breadcrumbs([
        {"label": "Trang chủ"},
        {"title": "Laptop"},
    ])
    assert out == ["Trang chủ", "Laptop"]


def test_breadcrumbs_object_missing_label_dropped():
    out = normalize_breadcrumbs([{"name": "A"}, {"foo": "bar"}, {"label": "B"}])
    assert out == ["A", "B"]


def test_breadcrumbs_whitespace_trimmed():
    out = normalize_breadcrumbs(["  Laptop  ", "  ", ""])
    assert out == ["Laptop"]


def test_breadcrumbs_in_document_round_trip():
    doc = build_document(_row(breadcrumbs=[{"name": "Trang chủ"}, {"name": "Laptop"}]))
    assert doc["breadcrumbs"] == ["Trang chủ", "Laptop"]
