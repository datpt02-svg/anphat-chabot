"""M2 pure mapping: ParsedRow -> {products_tuple, specs_tuple, spec_values,
prices_row, chunks_rows}. No DB, no IO.

Each output is exactly the column order required by the SQL in §4 of the plan.
This module is the only place where wide-column and JSONB layout is decided.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from scripts.m2_pipeline.config import SPECS_CHUNK_KEYS
from scripts.m2_pipeline.db import Jsonb
from scripts.m2_pipeline.hashing import (
    canonical_json,
    coerce_number,
    derive_group,
    extract_unit,
    hash_canonical,
    id_suffix_8,
    make_slug,
    price_hash_components,
    product_id_from_url,
    sha256_hex,
    slugify,
)
from scripts.m2_pipeline.parse import ParsedRow


# --- canonical_hash projection --------------------------------------------


def _stable_projection(row: ParsedRow) -> dict:
    """Stable business fields used to compute `products.canonical_hash`.

    Excludes timestamps, raw_html_path, validation_warnings, llm_warnings,
    status, and other volatile fields. Includes normalized_specs (full dict)
    so any LLM re-normalization changes the hash.
    """
    return {
        "name": row.name,
        "brand": row.brand,
        "category": row.category,
        "subcategory": row.subcategory,
        "thumbnail_url": row.thumbnail_url,
        "images": list(row.images),
        "price_vnd": row.sale_price_vnd if row.sale_price_vnd is not None else row.list_price_vnd,
        "list_price_vnd": row.list_price_vnd,
        "sale_price_vnd": row.sale_price_vnd,
        "build_pc_price_vnd": row.build_pc_price_vnd,
        "regional_price_vnd": row.regional_price_vnd,
        "stock_status": row.stock_status,
        "stock_quantity": row.stock_quantity,
        "warranty_text": row.warranty_text,
        "warranty_months": row.warranty_months,
        "description": row.description,
        "breadcrumbs": list(row.breadcrumbs),
        "raw_specs": row.raw_specs,
        "normalized_specs": row.normalized_specs,
    }


# --- chunk content builders ----------------------------------------------


def _build_title_chunk(row: ParsedRow) -> str:
    return f"{row.name} | brand={row.brand or 'N/A'} | category={row.category}"


def _build_description_chunk(row: ParsedRow) -> str:
    return row.description or ""


def _build_specs_chunk(row: ParsedRow) -> str:
    """Top-N wide columns in fixed order. Skip null. Join with '; '."""
    parts: list[str] = []
    for key in SPECS_CHUNK_KEYS:
        value = row.normalized_specs.get(key)
        if value is None:
            continue
        if isinstance(value, float):
            value = round(value, 4)
        parts.append(f"{key}: {value}")
    return "; ".join(parts)


def _build_raw_specs_chunk(row: ParsedRow) -> str:
    return canonical_json(row.raw_specs)


def _build_warranty_chunk(row: ParsedRow) -> str:
    if row.warranty_text:
        months = f" ({row.warranty_months} tháng)" if row.warranty_months is not None else ""
        return f"{row.warranty_text}{months}"
    return ""


_CHUNK_BUILDERS = (
    ("title", _build_title_chunk),
    ("description", _build_description_chunk),
    ("specs", _build_specs_chunk),
    ("raw_specs", _build_raw_specs_chunk),
    ("warranty", _build_warranty_chunk),
)


# --- spec_values builder -------------------------------------------------


def _build_spec_values_rows(row: ParsedRow) -> list[tuple]:
    """One row per scalar key, N rows per list key (spec_index 0..N-1).

    Per §9.2.1: skip `warnings` and `confidence` (they are not in spec_values).
    Per §Stage 5: `raw` = Jsonb({"src": "llm_normalized_specs"}),
    `confidence` from normalized_specs.confidence (or NULL).
    """
    confidence = row.normalized_specs.get("confidence")
    raw = {"src": "llm_normalized_specs"}

    out: list[tuple] = []
    for key, value in row.normalized_specs.items():
        if key in ("warnings", "confidence"):
            continue

        group = derive_group(key)
        unit = extract_unit(key)

        if isinstance(value, list):
            for i, element in enumerate(value):
                if element is None:
                    continue
                out.append((
                    row.product_id,
                    group,
                    key,
                    f"{key}[{i}]",
                    i,
                    str(element),
                    coerce_number(element),
                    unit,
                    confidence,
                    Jsonb(raw),
                ))
        elif value is not None:
            out.append((
                row.product_id,
                group,
                key,
                key,
                0,
                str(value),
                coerce_number(value),
                unit,
                confidence,
                Jsonb(raw),
            ))

    return out


# --- public output --------------------------------------------------------


@dataclass
class MappedProduct:
    products_tuple: tuple
    product_specs_tuple: tuple
    spec_values_rows: list[tuple] = field(default_factory=list)
    prices_row: Optional[dict] = None  # None if no captured_at can be derived
    chunks_rows: list[tuple] = field(default_factory=list)
    products_id: str = ""
    canonical_hash: str = ""


def map_product(row: ParsedRow) -> MappedProduct:
    """Transform one ParsedRow into all DB-ready rows for stages 3-7."""
    # --- derived identifiers --------------------------------------------
    product_id = product_id_from_url(row.source, row.source_url)
    slug = make_slug(row.name, row.source_url)
    canonical = hash_canonical(_stable_projection(row))

    # --- price derivation -----------------------------------------------
    price_vnd = row.sale_price_vnd if row.sale_price_vnd is not None else row.list_price_vnd
    p_hash = price_hash_components(
        price_vnd,
        row.list_price_vnd,
        row.sale_price_vnd,
        row.build_pc_price_vnd,
        row.regional_price_vnd,
        row.stock_status,
        row.stock_quantity,
    )

    # --- products tuple (30 cols) ---------------------------------------
    products_tuple = (
        product_id,
        row.source,
        row.source_url,
        row.source_product_id,
        row.sku,
        slug,
        row.name,
        row.brand,
        row.category,
        row.subcategory,
        row.thumbnail_url,
        Jsonb(list(row.images)),
        price_vnd,
        row.list_price_vnd,
        row.sale_price_vnd,
        row.build_pc_price_vnd,
        row.regional_price_vnd,
        row.stock_status,
        row.stock_quantity,
        row.warranty_text,
        row.warranty_months,
        row.description,
        Jsonb(list(row.breadcrumbs)),
        Jsonb(dict(row.raw_specs)),
        Jsonb(list(row.validation_warnings)),
        Jsonb(list(row.llm_warnings)),
        row.raw_html_path,
        canonical,
        row.crawled_at,
        row.normalized_at,
    )

    # --- product_specs tuple (45 cols) ----------------------------------
    ns = row.normalized_specs
    product_specs_tuple = (
        product_id,
        ns.get("product_type"),
        ns.get("model"),
        ns.get("cpu_model"),
        ns.get("cpu_cores"),
        ns.get("cpu_threads"),
        ns.get("cpu_base_clock_ghz"),
        ns.get("cpu_boost_clock_ghz"),
        ns.get("socket"),
        ns.get("ram_gb"),
        ns.get("ram_type"),
        ns.get("ram_speed_mhz"),
        ns.get("max_ram_gb"),
        ns.get("ram_slots"),
        ns.get("ram_standard"),
        ns.get("storage_gb"),
        ns.get("storage_type"),
        ns.get("storage_detail"),
        Jsonb(list(ns.get("upgrade_storage_options") or [])),
        ns.get("gpu_model"),
        ns.get("gpu_vram_gb"),
        ns.get("gpu_vram_type"),
        ns.get("chipset"),
        ns.get("form_factor"),
        ns.get("psu_wattage_w"),
        ns.get("recommended_psu_w"),
        Jsonb(list(ns.get("supported_mainboard_form_factors") or [])),
        ns.get("max_gpu_length_mm"),
        ns.get("max_cpu_cooler_height_mm"),
        ns.get("screen_inches"),
        ns.get("resolution_label"),
        ns.get("resolution_width"),
        ns.get("resolution_height"),
        ns.get("refresh_rate_hz"),
        ns.get("panel_type"),
        ns.get("os"),
        Jsonb(list(ns.get("ports") or [])),
        Jsonb(list(ns.get("connectivity") or [])),
        ns.get("switch_type"),
        ns.get("layout"),
        ns.get("mouse_dpi"),
        ns.get("weight_kg"),
        ns.get("confidence"),
        Jsonb(list(ns.get("warnings") or [])),
        Jsonb(dict(row.normalized_specs)),
    )

    # --- spec_values rows ----------------------------------------------
    spec_values_rows = _build_spec_values_rows(row)

    # --- prices row (captured_at from crawled_at, fallback now()) -----
    captured_at = row.crawled_at
    if captured_at is None:
        captured_at = datetime.now(timezone.utc)

    prices_row = {
        "product_id": product_id,
        "crawl_run_id": None,  # pipeline fills in the real UUID before SQL
        "price_vnd": price_vnd,
        "list_price_vnd": row.list_price_vnd,
        "sale_price_vnd": row.sale_price_vnd,
        "build_pc_price_vnd": row.build_pc_price_vnd,
        "regional_price_vnd": row.regional_price_vnd,
        "stock_status": row.stock_status,
        "stock_quantity": row.stock_quantity,
        "price_hash": p_hash,
        "captured_at": captured_at,
    }

    # --- chunks rows (5 per product) -----------------------------------
    chunks_rows: list[tuple] = []
    for chunk_type, builder in _CHUNK_BUILDERS:
        content = builder(row)
        ch_hash = sha256_hex(content)
        token_count = len(content) // 4
        chunks_rows.append((
            product_id,
            chunk_type,
            0,
            content,
            ch_hash,
            token_count,
            Jsonb({}),  # metadata
        ))

    return MappedProduct(
        products_tuple=products_tuple,
        product_specs_tuple=product_specs_tuple,
        spec_values_rows=spec_values_rows,
        prices_row=prices_row,
        chunks_rows=chunks_rows,
        products_id=product_id,
        canonical_hash=canonical,
    )


# --- count helper for tests (matches §9.2.1) --------------------------


def expected_spec_values_count(rows: list[dict]) -> int:
    """Number of `product_spec_values` rows that the spec_values stage will
    insert for a given list of input rows. Used by tests to assert exact count.
    """
    total = 0
    for row in rows:
        ns = row.get("normalized_specs") or {}
        for key, value in ns.items():
            if key in ("warnings", "confidence"):
                continue
            if isinstance(value, list):
                total += len(value)
            elif value is not None:
                total += 1
    return total
