"""M2 pipeline orchestrator.

psycopg 3.3 dropped `execute_values`. The pipeline uses:
- `cur.executemany()` for batch INSERT / UPSERT
- pre-check SELECTs for batch counts (inserted vs skipped/updated)
- A single multi-row CTE for raw_data and products to get RETURNING
  counts in one round-trip
- The unnest-based prices CTE (as locked in plan §4.6)

Per-batch: 1 transaction, ~7-10 SQLs. On batch failure, per-row replay
(N small transactions, max --batch-size). All counters in `crawl_runs.counts`
JSONB. Resume reuses --run-id, counts OVERWRITE (no merge).
"""
from __future__ import annotations

import json
import sys
import time
import traceback
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import psycopg

from scripts.m2_pipeline.config import (
    DEFAULT_BATCH_SIZE,
    DEFAULT_COMMIT_EVERY,
    DEFAULT_LOCK_TIMEOUT,
    DEFAULT_MAX_ROWS,
    DEFAULT_TIMEOUT_NORMAL,
)
from scripts.m2_pipeline.db import Jsonb, connect
from scripts.m2_pipeline.hashing import payload_hash
from scripts.m2_pipeline.mapping import map_product
from scripts.m2_pipeline.parse import (
    ParsedRow,
    check_file_size,
    load_products_json,
    parse_row,
)


# --- column lists (used in CTE placeholders) ----------------------------

PRODUCTS_COLS = [
    "id", "source", "source_url", "source_product_id", "sku", "slug", "name", "brand",
    "category", "subcategory", "thumbnail_url", "images",
    "price_vnd", "list_price_vnd", "sale_price_vnd", "build_pc_price_vnd", "regional_price_vnd",
    "stock_status", "stock_quantity", "warranty_text", "warranty_months",
    "description", "breadcrumbs", "raw_specs", "validation_warnings", "llm_warnings",
    "raw_html_path", "canonical_hash", "crawled_at", "normalized_at",
]

# JSONB columns inside the products tuple (positions 11, 22, 23, 24, 25).
PRODUCTS_JSONB_POS = {
    PRODUCTS_COLS.index("images"),
    PRODUCTS_COLS.index("breadcrumbs"),
    PRODUCTS_COLS.index("raw_specs"),
    PRODUCTS_COLS.index("validation_warnings"),
    PRODUCTS_COLS.index("llm_warnings"),
}


def _products_template() -> str:
    parts = []
    for i, _ in enumerate(PRODUCTS_COLS):
        parts.append("%s::jsonb" if i in PRODUCTS_JSONB_POS else "%s")
    return "(" + ", ".join(parts) + ")"


SPECS_COLS = [
    "product_id", "product_type", "model", "cpu_model", "cpu_cores", "cpu_threads",
    "cpu_base_clock_ghz", "cpu_boost_clock_ghz", "socket",
    "ram_gb", "ram_type", "ram_speed_mhz", "max_ram_gb", "ram_slots", "ram_standard",
    "storage_gb", "storage_type", "storage_detail", "upgrade_storage_options",
    "gpu_model", "gpu_vram_gb", "gpu_vram_type",
    "chipset", "form_factor", "psu_wattage_w", "recommended_psu_w",
    "supported_mainboard_form_factors", "max_gpu_length_mm", "max_cpu_cooler_height_mm",
    "screen_inches", "resolution_label", "resolution_width", "resolution_height",
    "refresh_rate_hz", "panel_type", "os", "ports", "connectivity",
    "switch_type", "layout", "mouse_dpi", "weight_kg",
    "confidence", "warnings", "specs",
]

SPECS_JSONB_POS = {
    SPECS_COLS.index("upgrade_storage_options"),
    SPECS_COLS.index("supported_mainboard_form_factors"),
    SPECS_COLS.index("ports"),
    SPECS_COLS.index("connectivity"),
    SPECS_COLS.index("warnings"),
    SPECS_COLS.index("specs"),
}

# Cast map: column name -> SQL cast suffix. Needed because VALUES (%s, NULL, %s)
# infers a uniform column type; if any value is NULL PG chooses text and the
# subsequent INSERT fails on bigint / integer / numeric / timestamptz columns.
PRODUCTS_CAST_MAP = {
    "price_vnd": "::bigint",
    "list_price_vnd": "::bigint",
    "sale_price_vnd": "::bigint",
    "build_pc_price_vnd": "::bigint",
    "regional_price_vnd": "::bigint",
    "stock_quantity": "::integer",
    "warranty_months": "::integer",
    "crawled_at": "::timestamptz",
    "normalized_at": "::timestamptz",
}

SPECS_CAST_MAP = {
    "cpu_cores": "::integer",
    "cpu_threads": "::integer",
    "cpu_base_clock_ghz": "::numeric",
    "cpu_boost_clock_ghz": "::numeric",
    "ram_gb": "::integer",
    "ram_speed_mhz": "::integer",
    "max_ram_gb": "::integer",
    "ram_slots": "::integer",
    "storage_gb": "::integer",
    "gpu_vram_gb": "::integer",
    "psu_wattage_w": "::integer",
    "recommended_psu_w": "::integer",
    "max_gpu_length_mm": "::integer",
    "max_cpu_cooler_height_mm": "::integer",
    "screen_inches": "::numeric",
    "resolution_width": "::integer",
    "resolution_height": "::integer",
    "refresh_rate_hz": "::integer",
    "mouse_dpi": "::integer",
    "weight_kg": "::numeric",
    "confidence": "::numeric",
}


def _make_template(cols, jsonb_pos, cast_map) -> str:
    parts = []
    for i, name in enumerate(cols):
        if i in jsonb_pos:
            parts.append("%s::jsonb")
        elif name in cast_map:
            parts.append("%s" + cast_map[name])
        else:
            parts.append("%s")
    return "(" + ", ".join(parts) + ")"


def _products_template() -> str:
    return _make_template(PRODUCTS_COLS, PRODUCTS_JSONB_POS, PRODUCTS_CAST_MAP)


def _specs_template() -> str:
    return _make_template(SPECS_COLS, SPECS_JSONB_POS, SPECS_CAST_MAP)


# --- SQL statements -------------------------------------------------------


SQL_RAW_DATA_BATCH = """
WITH input AS (
    SELECT * FROM (VALUES %s) AS v(
        run_id, source, source_url, source_file, line_number, payload, payload_hash
    )
),
ins AS (
    INSERT INTO raw_data (run_id, source, source_url, source_file, line_number, payload, payload_hash)
    SELECT run_id::uuid, source, source_url, source_file, line_number, payload, payload_hash
    FROM input v
    WHERE NOT EXISTS (
        SELECT 1 FROM raw_data r
        WHERE r.source_url = v.source_url AND r.payload_hash = v.payload_hash
    )
    RETURNING id
)
SELECT count(*) AS c FROM ins
"""

RAW_DATA_VALUE_TEMPLATE = "(%s, %s, %s, %s, %s, %s::jsonb, %s)"


def _sql_products_batch(template: str) -> str:
    """Build a single-SQL multi-row UPSERT for products.

    Uses VALUES %s where %s is replaced with N copies of `template`.
    Note: new vs updated is tracked via a pre-check SELECT (see _process_row),
    not via the (xmax = 0) RETURNING trick which is unreliable inside CTEs.
    """
    col_list = ", ".join(PRODUCTS_COLS)
    v_col_list = ", ".join(PRODUCTS_COLS)
    return f"""
WITH input AS (
    SELECT * FROM (VALUES %s) AS v({v_col_list})
)
INSERT INTO products ({col_list})
SELECT {col_list} FROM input
ON CONFLICT (id) DO UPDATE SET
    name = EXCLUDED.name,
    brand = COALESCE(EXCLUDED.brand, products.brand),
    category = EXCLUDED.category,
    subcategory = COALESCE(EXCLUDED.subcategory, products.subcategory),
    thumbnail_url = COALESCE(EXCLUDED.thumbnail_url, products.thumbnail_url),
    images = EXCLUDED.images,
    price_vnd = EXCLUDED.price_vnd,
    list_price_vnd = EXCLUDED.list_price_vnd,
    sale_price_vnd = EXCLUDED.sale_price_vnd,
    build_pc_price_vnd = EXCLUDED.build_pc_price_vnd,
    regional_price_vnd = EXCLUDED.regional_price_vnd,
    stock_status = EXCLUDED.stock_status,
    stock_quantity = EXCLUDED.stock_quantity,
    warranty_text = COALESCE(EXCLUDED.warranty_text, products.warranty_text),
    warranty_months = COALESCE(EXCLUDED.warranty_months, products.warranty_months),
    description = COALESCE(EXCLUDED.description, products.description),
    breadcrumbs = EXCLUDED.breadcrumbs,
    raw_specs = EXCLUDED.raw_specs,
    validation_warnings = EXCLUDED.validation_warnings,
    llm_warnings = EXCLUDED.llm_warnings,
    raw_html_path = COALESCE(EXCLUDED.raw_html_path, products.raw_html_path),
    canonical_hash = EXCLUDED.canonical_hash,
    crawled_at = COALESCE(EXCLUDED.crawled_at, products.crawled_at),
    normalized_at = COALESCE(EXCLUDED.normalized_at, products.normalized_at),
    updated_at = now()
"""


def _sql_specs_batch(template: str) -> str:
    col_list = ", ".join(SPECS_COLS)
    v_col_list = ", ".join(SPECS_COLS)
    return f"""
WITH input AS (
    SELECT * FROM (VALUES %s) AS v({v_col_list})
),
ins AS (
    INSERT INTO product_specs ({col_list})
    SELECT {col_list} FROM input
    ON CONFLICT (product_id) DO UPDATE SET
        product_type = COALESCE(EXCLUDED.product_type, product_specs.product_type),
        model = COALESCE(EXCLUDED.model, product_specs.model),
        cpu_model = COALESCE(EXCLUDED.cpu_model, product_specs.cpu_model),
        cpu_cores = COALESCE(EXCLUDED.cpu_cores, product_specs.cpu_cores),
        cpu_threads = COALESCE(EXCLUDED.cpu_threads, product_specs.cpu_threads),
        cpu_base_clock_ghz = COALESCE(EXCLUDED.cpu_base_clock_ghz, product_specs.cpu_base_clock_ghz),
        cpu_boost_clock_ghz = COALESCE(EXCLUDED.cpu_boost_clock_ghz, product_specs.cpu_boost_clock_ghz),
        socket = COALESCE(EXCLUDED.socket, product_specs.socket),
        ram_gb = COALESCE(EXCLUDED.ram_gb, product_specs.ram_gb),
        ram_type = COALESCE(EXCLUDED.ram_type, product_specs.ram_type),
        ram_speed_mhz = COALESCE(EXCLUDED.ram_speed_mhz, product_specs.ram_speed_mhz),
        max_ram_gb = COALESCE(EXCLUDED.max_ram_gb, product_specs.max_ram_gb),
        ram_slots = COALESCE(EXCLUDED.ram_slots, product_specs.ram_slots),
        ram_standard = COALESCE(EXCLUDED.ram_standard, product_specs.ram_standard),
        storage_gb = COALESCE(EXCLUDED.storage_gb, product_specs.storage_gb),
        storage_type = COALESCE(EXCLUDED.storage_type, product_specs.storage_type),
        storage_detail = COALESCE(EXCLUDED.storage_detail, product_specs.storage_detail),
        gpu_model = COALESCE(EXCLUDED.gpu_model, product_specs.gpu_model),
        gpu_vram_gb = COALESCE(EXCLUDED.gpu_vram_gb, product_specs.gpu_vram_gb),
        gpu_vram_type = COALESCE(EXCLUDED.gpu_vram_type, product_specs.gpu_vram_type),
        chipset = COALESCE(EXCLUDED.chipset, product_specs.chipset),
        form_factor = COALESCE(EXCLUDED.form_factor, product_specs.form_factor),
        psu_wattage_w = COALESCE(EXCLUDED.psu_wattage_w, product_specs.psu_wattage_w),
        recommended_psu_w = COALESCE(EXCLUDED.recommended_psu_w, product_specs.recommended_psu_w),
        max_gpu_length_mm = COALESCE(EXCLUDED.max_gpu_length_mm, product_specs.max_gpu_length_mm),
        max_cpu_cooler_height_mm = COALESCE(EXCLUDED.max_cpu_cooler_height_mm, product_specs.max_cpu_cooler_height_mm),
        screen_inches = COALESCE(EXCLUDED.screen_inches, product_specs.screen_inches),
        resolution_label = COALESCE(EXCLUDED.resolution_label, product_specs.resolution_label),
        resolution_width = COALESCE(EXCLUDED.resolution_width, product_specs.resolution_width),
        resolution_height = COALESCE(EXCLUDED.resolution_height, product_specs.resolution_height),
        refresh_rate_hz = COALESCE(EXCLUDED.refresh_rate_hz, product_specs.refresh_rate_hz),
        panel_type = COALESCE(EXCLUDED.panel_type, product_specs.panel_type),
        os = COALESCE(EXCLUDED.os, product_specs.os),
        switch_type = COALESCE(EXCLUDED.switch_type, product_specs.switch_type),
        layout = COALESCE(EXCLUDED.layout, product_specs.layout),
        mouse_dpi = COALESCE(EXCLUDED.mouse_dpi, product_specs.mouse_dpi),
        weight_kg = COALESCE(EXCLUDED.weight_kg, product_specs.weight_kg),
        confidence = COALESCE(EXCLUDED.confidence, product_specs.confidence),
        upgrade_storage_options = EXCLUDED.upgrade_storage_options,
        supported_mainboard_form_factors = EXCLUDED.supported_mainboard_form_factors,
        ports = EXCLUDED.ports,
        connectivity = EXCLUDED.connectivity,
        warnings = EXCLUDED.warnings,
        specs = EXCLUDED.specs,
        updated_at = now()
    RETURNING product_id
)
SELECT product_id FROM ins
"""


SQL_SPEC_VALUES_DELETE = "DELETE FROM product_spec_values WHERE product_id = ANY(%s::text[])"

SQL_SPEC_VALUES_INSERT = """
INSERT INTO product_spec_values (
    product_id, group_name, spec_key, normalized_key, spec_index,
    spec_value, value_num, unit, confidence, raw
) VALUES %s
"""

SPEC_VALUES_VALUE_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb)"


SQL_PRICES_APPEND = """
WITH last AS (
    SELECT DISTINCT ON (product_id) product_id, price_hash
    FROM product_prices
    WHERE product_id = ANY(%s::text[])
    ORDER BY product_id, captured_at DESC, created_at DESC
),
batch AS (
    SELECT
        t.product_id, t.crawl_run_id, t.price_vnd, t.list_price_vnd, t.sale_price_vnd,
        t.build_pc_price_vnd, t.regional_price_vnd, t.stock_status, t.stock_quantity,
        t.price_hash, t.captured_at
    FROM unnest(
        %s::text[], %s::uuid[], %s::bigint[], %s::bigint[], %s::bigint[],
        %s::bigint[], %s::bigint[], %s::text[], %s::integer[], %s::text[],
        %s::timestamptz[]
    ) AS t(product_id, crawl_run_id, price_vnd, list_price_vnd, sale_price_vnd,
           build_pc_price_vnd, regional_price_vnd, stock_status, stock_quantity,
           price_hash, captured_at)
),
ins AS (
    INSERT INTO product_prices (
        product_id, crawl_run_id, price_vnd, list_price_vnd, sale_price_vnd,
        build_pc_price_vnd, regional_price_vnd, stock_status, stock_quantity,
        price_hash, captured_at
    )
    SELECT * FROM batch
    WHERE NOT EXISTS (
        SELECT 1 FROM last l
        WHERE l.product_id = batch.product_id
          AND l.price_hash = batch.price_hash
    )
    RETURNING id
)
SELECT count(*) AS inserted_count FROM ins
"""


SQL_CHUNKS_UPSERT = """
INSERT INTO product_chunks (
    product_id, chunk_type, chunk_index, content, content_hash, token_count, metadata
) VALUES %s
ON CONFLICT (product_id, chunk_type, chunk_index, content_hash) DO NOTHING
"""

CHUNKS_VALUE_TEMPLATE = "(%s, %s, %s, %s, %s, %s, %s::jsonb)"


SQL_CRAWL_ERRORS_INSERT = """
INSERT INTO crawl_errors (run_id, source_url, stage, error_type, error_message, raw)
VALUES (%s, %s, %s, %s, %s, %s)
"""


# --- counts model --------------------------------------------------------


def _new_counts() -> dict:
    return {
        "input_rows": 0,
        "parsed_ok": 0,
        "skipped_missing_fields": 0,
        "products": {
            "upserted": 0,
            "inserted_new": 0,
            "updated_existing": 0,
            "id_collisions": 0,
            "url_collisions": 0,
            "slug_collisions": 0,
        },
        "raw_data": {"inserted": 0, "skipped_dup_payload": 0},
        "specs": {"upserted": 0, "wide_columns_filled": 0, "wide_columns_null": 0},
        "spec_values": {"deleted_rows": 0, "inserted_rows": 0, "products_replaced": 0},
        "prices": {
            "appended": 0,
            "skipped_same_hash": 0,
            "captured_at_from_crawled_at": 0,
            "captured_at_fallback_now": 0,
        },
        "chunks": {
            "inserted": 0,
            "skipped_dup": 0,
            "by_type": {
                "title": 0,
                "description": 0,
                "specs": 0,
                "raw_specs": 0,
                "warranty": 0,
            },
        },
        "errors": {"total": 0, "by_stage": {}, "by_type": {}},
        "duration_seconds": 0.0,
        "batches_committed": 0,
        "batches_failed_then_recovered": 0,
        "row_level_failures": 0,
    }


# --- progress emit ------------------------------------------------------


def emit_progress(stage: str, batch_id: int, rows_done: int, rows_total: int,
                  start_time: float) -> None:
    elapsed = max(0.001, time.time() - start_time)
    rate = rows_done / elapsed if elapsed > 0 else 0
    remaining = max(0, rows_total - rows_done)
    eta = int(remaining / rate) if rate > 0 else 0
    line = (
        f'M2_PROGRESS {{"ts": "{datetime.now(timezone.utc).isoformat()}", '
        f'"stage": "{stage}", "batch": {batch_id}, "rows_done": {rows_done}, '
        f'"rows_total": {rows_total}, "rate_rows_per_sec": {rate:.0f}, '
        f'"eta_seconds": {eta}}}'
    )
    print(line, file=sys.stderr, flush=True)


# --- helpers ------------------------------------------------------------


def _record_error(cur: psycopg.Cursor, run_id: str, source_url: str,
                  stage: str, exc: BaseException, counts: dict) -> None:
    try:
        cur.execute(
            SQL_CRAWL_ERRORS_INSERT,
            (
                run_id, source_url, stage, type(exc).__name__,
                str(exc)[:1000],
                Jsonb({
                    "row_index": -1,
                    "traceback": traceback.format_exc()[-1000:],
                }),
            ),
        )
    except Exception:
        pass

    counts["errors"]["total"] += 1
    counts["errors"]["by_stage"][stage] = counts["errors"]["by_stage"].get(stage, 0) + 1
    counts["errors"]["by_type"][type(exc).__name__] = (
        counts["errors"]["by_type"].get(type(exc).__name__, 0) + 1
    )
    counts["row_level_failures"] += 1


def _merge_counts(main: dict, addition: dict) -> None:
    """In-place merge `addition` into `main` (both have _new_counts() shape)."""
    for category in ("products", "raw_data", "specs", "spec_values", "prices", "chunks"):
        for key, val in addition[category].items():
            if isinstance(val, dict):
                for k2, v2 in val.items():
                    main[category][key][k2] = main[category][key].get(k2, 0) + v2
            else:
                main[category][key] = main[category].get(key, 0) + val
    main["batches_committed"] += addition["batches_committed"]
    main["batches_failed_then_recovered"] += addition["batches_failed_then_recovered"]
    main["row_level_failures"] += addition["row_level_failures"]


def _count_wide_columns(specs_tuple: tuple, counts: dict) -> None:
    """Count non-null wide columns in the 38 scalars + confidence (positions 1-38, 42)."""
    filled = 0
    total = 0
    scalar_indexes = list(range(1, 39)) + [SPECS_COLS.index("confidence")]
    for i in scalar_indexes:
        total += 1
        if i < len(specs_tuple) and specs_tuple[i] is not None:
            filled += 1
    counts["specs"]["wide_columns_filled"] += filled
    counts["specs"]["wide_columns_null"] += (total - filled)


def _build_values_params(template: str, rows: list[tuple]) -> tuple[str, list]:
    """Build a (VALUES %s, params) pair for a multi-row CTE.

    Example: template="(%s, %s, %s)", rows=[(1,'a',1.5),(2,'b',2.5)]
      -> ( "(%s, %s, %s), (%s, %s, %s)", [1,'a',1.5,2,'b',2.5] )
    """
    if not rows:
        return "", []
    placeholders = ",".join([template] * len(rows))
    flat: list = []
    for row in rows:
        flat.extend(row)
    return placeholders, flat


# --- per-row processing -------------------------------------------------


def _process_row(
    cur: psycopg.Cursor,
    run_id: str,
    parsed: ParsedRow,
    existing_product_ids: set,
    counts: dict,
    source_file: str,
) -> None:
    """Run stages 2-7 for a single row inside an open transaction.

    `existing_product_ids` is the pre-check set computed ONCE at the start
    of the batch transaction (before any INSERTs). Using a per-row SELECT
    inside the same transaction would see the batch's own uncommitted
    writes and misclassify every row as 'updated_existing'.
    """
    mapped = map_product(parsed)

    # Stage 2: raw_data (1 row, multi-row CTE)
    p_hash = payload_hash(parsed.raw)
    raw_rows = [(
        run_id, parsed.source, parsed.source_url, source_file,
        parsed.row_index, Jsonb(parsed.raw), p_hash,
    )]
    values_sql, params = _build_values_params(RAW_DATA_VALUE_TEMPLATE, raw_rows)
    sql = SQL_RAW_DATA_BATCH.replace("%s", values_sql, 1)
    cur.execute(sql, params)
    inserted_raw = cur.fetchone()["c"] or 0
    counts["raw_data"]["inserted"] += inserted_raw
    counts["raw_data"]["skipped_dup_payload"] += len(raw_rows) - inserted_raw

    # Stage 3: products (pre-check via batch-level set, then multi-row UPSERT)
    is_new = mapped.products_id not in existing_product_ids

    template = _products_template()
    values_sql, params = _build_values_params(template, [mapped.products_tuple])
    sql = _sql_products_batch(template).replace("%s", values_sql, 1)
    cur.execute(sql, params)

    if is_new:
        counts["products"]["inserted_new"] += 1
    else:
        counts["products"]["updated_existing"] += 1
    counts["products"]["upserted"] += 1

    # Stage 4: specs (multi-row CTE)
    template = _specs_template()
    values_sql, params = _build_values_params(template, [mapped.product_specs_tuple])
    sql = _sql_specs_batch(template).replace("%s", values_sql, 1)
    cur.execute(sql, params)
    counts["specs"]["upserted"] += 1
    _count_wide_columns(mapped.product_specs_tuple, counts)

    # Stage 5: spec_values (DELETE + INSERT)
    if mapped.spec_values_rows:
        cur.execute(SQL_SPEC_VALUES_DELETE, ([mapped.products_id],))
        counts["spec_values"]["deleted_rows"] += cur.rowcount or 0

        values_sql, params = _build_values_params(SPEC_VALUES_VALUE_TEMPLATE, mapped.spec_values_rows)
        sql = SQL_SPEC_VALUES_INSERT.replace("%s", values_sql, 1)
        cur.execute(sql, params)
        counts["spec_values"]["inserted_rows"] += len(mapped.spec_values_rows)
        counts["spec_values"]["products_replaced"] += 1

    # Stage 6: prices (single query unnest)
    captured_at = parsed.crawled_at or datetime.now(timezone.utc)
    if parsed.crawled_at is not None:
        counts["prices"]["captured_at_from_crawled_at"] += 1
    else:
        counts["prices"]["captured_at_fallback_now"] += 1

    pr = mapped.prices_row
    product_ids_arr = [pr["product_id"]]
    run_id_arr = [run_id]
    arrays = [
        product_ids_arr,                     # for ANY
        product_ids_arr,                     # unnest
        run_id_arr,                          # unnest
        [pr["price_vnd"]],
        [pr["list_price_vnd"]],
        [pr["sale_price_vnd"]],
        [pr["build_pc_price_vnd"]],
        [pr["regional_price_vnd"]],
        [pr["stock_status"]],
        [pr["stock_quantity"]],
        [pr["price_hash"]],
        [captured_at],
    ]
    cur.execute(SQL_PRICES_APPEND, arrays)
    ins_prices = cur.fetchone()["inserted_count"] or 0
    counts["prices"]["appended"] += ins_prices
    counts["prices"]["skipped_same_hash"] += 1 - ins_prices

    # Stage 7: chunks (multi-row INSERT ON CONFLICT DO NOTHING)
    # Pre-check which (product_id, chunk_type, content_hash) already exist.
    product_id = mapped.products_id
    cur.execute(
        "SELECT chunk_type, content_hash FROM product_chunks "
        "WHERE product_id = %s AND chunk_index = 0",
        (product_id,),
    )
    existing = {(r["chunk_type"], r["content_hash"]) for r in cur.fetchall()}

    values_sql, params = _build_values_params(CHUNKS_VALUE_TEMPLATE, mapped.chunks_rows)
    sql = SQL_CHUNKS_UPSERT.replace("%s", values_sql, 1)
    cur.execute(sql, params)

    for row in mapped.chunks_rows:
        ctype, chash = row[1], row[4]
        if (ctype, chash) in existing:
            counts["chunks"]["skipped_dup"] += 1
        else:
            counts["chunks"]["inserted"] += 1
            counts["chunks"]["by_type"][ctype] = counts["chunks"]["by_type"].get(ctype, 0) + 1


# --- batch processing ---------------------------------------------------


def _process_batch_dry(rows: list[ParsedRow], counts: dict) -> None:
    """Dry-run: count everything as if all succeeded."""
    for parsed in rows:
        mapped = map_product(parsed)
        counts["raw_data"]["inserted"] += 1
        counts["products"]["inserted_new"] += 1
        counts["products"]["upserted"] += 1
        counts["specs"]["upserted"] += 1
        _count_wide_columns(mapped.product_specs_tuple, counts)
        counts["spec_values"]["inserted_rows"] += len(mapped.spec_values_rows)
        counts["spec_values"]["products_replaced"] += 1
        counts["prices"]["appended"] += 1
        if parsed.crawled_at is not None:
            counts["prices"]["captured_at_from_crawled_at"] += 1
        else:
            counts["prices"]["captured_at_fallback_now"] += 1
        counts["chunks"]["inserted"] += len(mapped.chunks_rows)
        for ctype in ("title", "description", "specs", "raw_specs", "warranty"):
            counts["chunks"]["by_type"][ctype] += 1


# --- public options + entrypoint ----------------------------------------


@dataclass
class PipelineOptions:
    input_path: Path
    source: str
    batch_size: int = DEFAULT_BATCH_SIZE
    commit_every: int = DEFAULT_COMMIT_EVERY
    max_rows: Optional[int] = DEFAULT_MAX_ROWS
    run_id: Optional[str] = None
    dry_run: bool = False
    json_progress: bool = False


@dataclass
class PipelineResult:
    run_id: str
    status: str
    counts: dict
    error_message: Optional[str] = None
    duration_seconds: float = 0.0


def run_pipeline(opts: PipelineOptions) -> PipelineResult:
    counts = _new_counts()
    start = time.time()
    error_message: Optional[str] = None
    run_id: Optional[str] = None

    # Stage 1: load + validate
    raw_rows: list[dict] = []
    parse_time_error: Optional[str] = None
    try:
        check_file_size(opts.input_path)
        raw_rows = load_products_json(opts.input_path)
    except (FileNotFoundError, ValueError, json.JSONDecodeError) as exc:
        parse_time_error = f"input file error: {exc}"

    if opts.max_rows is not None and opts.max_rows > 0:
        raw_rows = raw_rows[: opts.max_rows]

    counts["input_rows"] = len(raw_rows)
    if parse_time_error is not None:
        return PipelineResult(
            run_id="",
            status="failed",
            counts=counts,
            error_message=parse_time_error,
            duration_seconds=time.time() - start,
        )

    parsed_rows: list[ParsedRow] = []
    for raw in raw_rows:
        p = parse_row(raw, row_index=len(parsed_rows) + 1, default_source=opts.source)
        if p is None:
            counts["skipped_missing_fields"] += 1
        else:
            parsed_rows.append(p)
    counts["parsed_ok"] = len(parsed_rows)

    if opts.dry_run:
        if parsed_rows:
            _process_batch_dry(parsed_rows, counts)
        counts["duration_seconds"] = time.time() - start
        return PipelineResult(
            run_id="dry-run",
            status="done",
            counts=counts,
            error_message=None,
            duration_seconds=counts["duration_seconds"],
        )

    source_file = str(opts.input_path)
    status = "done"

    try:
        with connect() as conn:
            # Stage 0: init run
            if opts.run_id is not None:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE crawl_runs SET status = 'running' WHERE id = %s RETURNING id",
                        (opts.run_id,),
                    )
                    row = cur.fetchone()
                    if row is None:
                        raise RuntimeError(f"--run-id {opts.run_id} not found in crawl_runs")
                    run_id = str(row["id"])
            else:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO crawl_runs (source, status, input_paths, config)
                        VALUES (%s, 'running', %s, %s)
                        RETURNING id
                        """,
                        (
                            opts.source,
                            Jsonb({"products": source_file}),
                            Jsonb({
                                "batch_size": opts.batch_size,
                                "commit_every": opts.commit_every,
                                "m2": True,
                            }),
                        ),
                    )
                    run_id = str(cur.fetchone()["id"])
            conn.commit()

            # Stages 2-7 batch loop
            total_rows = len(parsed_rows)
            rows_done = 0
            batch_id = 0
            for batch_start in range(0, total_rows, opts.batch_size):
                batch_id += 1
                batch = parsed_rows[batch_start : batch_start + opts.batch_size]
                if not batch:
                    continue

                # Per-batch counter: discard on transaction rollback.
                batch_counts = _new_counts()
                try:
                    with conn.transaction():
                        with conn.cursor() as cur:
                            cur.execute(f"SET LOCAL statement_timeout = '{DEFAULT_TIMEOUT_NORMAL}'")
                            cur.execute(f"SET LOCAL lock_timeout = '{DEFAULT_LOCK_TIMEOUT}'")
                            # Pre-check existing product_ids ONCE per batch, BEFORE
                            # any INSERTs, so the set reflects committed state only.
                            batch_pids = [p.product_id for p in batch]
                            cur.execute(
                                "SELECT id FROM products WHERE id = ANY(%s)",
                                (batch_pids,),
                            )
                            existing_pids = {r["id"] for r in cur.fetchall()}
                            for parsed in batch:
                                _process_row(cur, run_id, parsed, existing_pids, batch_counts, source_file)
                    # Batch committed: merge batch_counts into main counts.
                    _merge_counts(counts, batch_counts)
                    counts["batches_committed"] += 1
                except psycopg.Error as exc:
                    print(
                        f"batch {batch_id} failed, replaying per-row "
                        f"(max {opts.batch_size} transactions): {exc}",
                        file=sys.stderr,
                    )
                    counts["batches_failed_then_recovered"] += 1
                    for parsed in batch:
                        try:
                            with conn.transaction():
                                with conn.cursor() as cur:
                                    cur.execute(
                                        f"SET LOCAL statement_timeout = '{DEFAULT_TIMEOUT_NORMAL}'"
                                    )
                                    cur.execute(
                                        f"SET LOCAL lock_timeout = '{DEFAULT_LOCK_TIMEOUT}'"
                                    )
                                    # Single-row pre-check (no batch context here)
                                    cur.execute(
                                        "SELECT id FROM products WHERE id = %s",
                                        (parsed.product_id,),
                                    )
                                    existing = {r["id"] for r in cur.fetchall()}
                                    _process_row(cur, run_id, parsed, existing, counts, source_file)
                        except psycopg.Error as row_exc:
                            with conn.cursor() as cur:
                                _record_error(cur, run_id, parsed.source_url,
                                              "batch_row", row_exc, counts)

                rows_done += len(batch)
                if opts.json_progress:
                    emit_progress("batch", batch_id, rows_done, total_rows, start)

            # Stage 8: finalize
            counts["duration_seconds"] = time.time() - start
            if counts["row_level_failures"] > 0:
                pct = counts["row_level_failures"] / max(1, counts["input_rows"])
                if pct >= 0.01 or counts["parsed_ok"] / max(1, counts["input_rows"]) < 0.95:
                    status = "failed"
                else:
                    status = "partial"
            else:
                status = "done"

            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE crawl_runs
                    SET status = %s,
                        finished_at = now(),
                        total_crawled = %s,
                        total_normalized = %s,
                        total_failed = %s,
                        counts = %s,
                        error_message = %s
                    WHERE id = %s
                    """,
                    (
                        status,
                        counts["parsed_ok"],
                        counts["parsed_ok"],
                        counts["row_level_failures"],
                        Jsonb(counts),
                        error_message,
                        run_id,
                    ),
                )
            conn.commit()
    except Exception as exc:
        status = "failed"
        error_message = f"{type(exc).__name__}: {exc}"
        counts["duration_seconds"] = time.time() - start

    return PipelineResult(
        run_id=run_id or "",
        status=status,
        counts=counts,
        error_message=error_message,
        duration_seconds=counts["duration_seconds"],
    )
