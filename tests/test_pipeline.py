"""Integration tests for the M2 pipeline (DB required, marker `integration`).

Run with:
    uv run pytest -q -m integration
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import psycopg
import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m2_pipeline.mapping import expected_spec_values_count  # noqa: E402
from scripts.m2_pipeline.pipeline import (  # noqa: E402
    PipelineOptions,
    run_pipeline,
)


pytestmark = pytest.mark.integration


# --- helpers --------------------------------------------------------------


def _load(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _count_products(source: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM products WHERE source = %s", (source,)
            )
            return cur.fetchone()["c"]


def _count_crawl_errors(run_id: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM crawl_errors WHERE run_id = %s", (run_id,)
            )
            return cur.fetchone()["c"]


# --- test: dry-run --------------------------------------------------------


def test_dry_run_no_db_writes(clean_source, fixture_path):
    fixture = fixture_path / "products_m2_minimal.json"
    rows = _load(fixture)
    expected_chunks = 5 * sum(1 for r in rows if r.get("name"))

    result = run_pipeline(
        PipelineOptions(input_path=fixture, source=clean_source, dry_run=True)
    )

    assert result.status == "done"
    assert result.run_id == "dry-run"
    assert result.counts["input_rows"] == 6
    assert result.counts["parsed_ok"] == 5
    assert result.counts["skipped_missing_fields"] == 1
    assert result.counts["products"]["upserted"] == 5
    assert result.counts["chunks"]["inserted"] == expected_chunks

    # No DB writes happened
    assert _count_products(clean_source) == 0


# --- test: first import of minimal fixture -------------------------------


def test_first_import_minimal(clean_source, fixture_path):
    fixture = fixture_path / "products_m2_minimal.json"
    rows = _load(fixture)
    expected_specs = expected_spec_values_count(rows)
    valid_count = sum(1 for r in rows if r.get("name"))

    result = run_pipeline(PipelineOptions(input_path=fixture, source=clean_source))

    assert result.counts["input_rows"] == 6
    assert result.counts["parsed_ok"] == 5
    assert result.counts["skipped_missing_fields"] == 1
    assert result.counts["products"]["upserted"] == 5
    assert result.counts["products"]["inserted_new"] == 5
    assert result.counts["products"]["updated_existing"] == 0
    assert result.counts["raw_data"]["inserted"] == 5
    assert result.counts["raw_data"]["skipped_dup_payload"] == 0
    assert result.counts["specs"]["upserted"] == 5
    assert result.counts["spec_values"]["inserted_rows"] == expected_specs
    assert result.counts["spec_values"]["products_replaced"] == 5
    assert result.counts["chunks"]["inserted"] == 5 * valid_count
    assert result.counts["errors"]["total"] == 0
    assert result.status == "done"

    # crawl_errors is empty (parse-time fail does NOT write crawl_errors per §7.0)
    assert _count_crawl_errors(result.run_id) == 0

    # DB sanity
    assert _count_products(clean_source) == 5

    # spec_values count from DB matches expected
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM product_spec_values sv "
                "JOIN products p ON p.id = sv.product_id "
                "WHERE p.source = %s",
                (clean_source,),
            )
            assert cur.fetchone()["c"] == expected_specs


# --- test: re-run idempotency --------------------------------------------


def test_rerun_idempotent(clean_source, fixture_path):
    fixture = fixture_path / "products_m2_minimal.json"
    opts = PipelineOptions(input_path=fixture, source=clean_source)

    r1 = run_pipeline(opts)
    r2 = run_pipeline(opts)

    # 1st run inserted everything
    assert r1.counts["products"]["inserted_new"] == 5
    assert r1.counts["chunks"]["inserted"] == 25

    # 2nd run: no new rows
    assert r2.counts["products"]["inserted_new"] == 0
    assert r2.counts["products"]["updated_existing"] == 5
    assert r2.counts["raw_data"]["inserted"] == 0
    assert r2.counts["raw_data"]["skipped_dup_payload"] == 5
    assert r2.counts["prices"]["appended"] == 0
    assert r2.counts["prices"]["skipped_same_hash"] == 5
    assert r2.counts["chunks"]["inserted"] == 0
    assert r2.counts["chunks"]["skipped_dup"] == 25

    # products count still 5
    assert _count_products(clean_source) == 5


# --- test: mutation (A then B with same product_id) ----------------------


def test_mutation_a_then_b(clean_source, fixture_path):
    a = fixture_path / "products_m2_resume_a.json"
    b = fixture_path / "products_m2_resume_b.json"

    r1 = run_pipeline(PipelineOptions(input_path=a, source=clean_source))
    r2 = run_pipeline(PipelineOptions(input_path=b, source=clean_source))

    # A: 3 new
    assert r1.counts["products"]["upserted"] == 3
    assert r1.counts["products"]["inserted_new"] == 3
    # B: 1 updated (A.0), 1 new (B.1)
    assert r2.counts["products"]["upserted"] == 2
    assert r2.counts["products"]["inserted_new"] == 1
    assert r2.counts["products"]["updated_existing"] == 1

    # Verify A.0/B.0 in DB has new values
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT p.id, p.sale_price_vnd, ps.ram_gb, p.description "
                "FROM products p JOIN product_specs ps ON ps.product_id = p.id "
                "WHERE p.source = %s AND p.source_url LIKE %s",
                (clean_source, "%resume-a-0%"),
            )
            row = cur.fetchone()
            assert row is not None
            assert row["sale_price_vnd"] == 18000000  # 18M (was 20M)
            assert row["ram_gb"] == 32  # was 16
            assert "MUTATED" in row["description"]

            # product_prices for A.0/B.0 has 2 rows (old + new)
            cur.execute(
                "SELECT count(*) AS c FROM product_prices WHERE product_id = %s",
                (row["id"],),
            )
            assert cur.fetchone()["c"] == 2

            # spec_values for A.0/B.0 only reflect B's values (DELETE+INSERT)
            cur.execute(
                "SELECT count(*) AS c FROM product_spec_values WHERE product_id = %s",
                (row["id"],),
            )
            b_specs = cur.fetchone()["c"]
            # B fixture row 0 normalized_specs: brand, category, product_type, model,
            # cpu_model, cpu_cores, cpu_threads, ram_gb, ram_type, storage_gb,
            # storage_type, warranty_months = 12 scalars, 0 lists = 12
            assert b_specs == 12

            # B.1 (new) exists
            cur.execute(
                "SELECT count(*) AS c FROM products WHERE source = %s AND source_url LIKE %s",
                (clean_source, "%resume-b-1%"),
            )
            assert cur.fetchone()["c"] == 1


# --- test: --resume reuses run_id, counts overwrite --------------------


def test_resume_overwrite_counts(clean_source, fixture_path):
    a = fixture_path / "products_m2_resume_a.json"
    b = fixture_path / "products_m2_resume_b.json"

    r1 = run_pipeline(PipelineOptions(input_path=a, source=clean_source))
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT started_at FROM crawl_runs WHERE id = %s", (r1.run_id,)
            )
            started_at_1 = cur.fetchone()["started_at"]

    time.sleep(0.1)

    r2 = run_pipeline(
        PipelineOptions(input_path=b, source=clean_source, run_id=r1.run_id)
    )

    with connect() as conn:
        with conn.cursor() as cur:
            # Only 1 crawl_runs row
            cur.execute(
                "SELECT count(*) AS c FROM crawl_runs WHERE source = %s",
                (clean_source,),
            )
            assert cur.fetchone()["c"] == 1
            # started_at preserved
            cur.execute(
                "SELECT started_at, finished_at FROM crawl_runs WHERE id = %s",
                (r1.run_id,),
            )
            row = cur.fetchone()
            assert row["started_at"] == started_at_1
            assert row["finished_at"] > row["started_at"]

    # Counts overwrite (only invocation 2's results)
    assert r2.counts["products"]["upserted"] == 2
    assert r2.counts["products"]["inserted_new"] == 1
    assert r2.counts["products"]["updated_existing"] == 1
    assert r2.counts["raw_data"]["inserted"] == 2


# --- test: stage error (monkeypatched) ---------------------------------


def test_stage_error_records_crawl_error(clean_source, fixture_path, monkeypatch):
    from scripts.m2_pipeline import pipeline as pipeline_mod

    original_process = pipeline_mod._process_row

    def patched_process(cur, run_id, parsed, existing, counts, source_file):
        # Force row_index=2 to raise (per §9.2 stage error test)
        if parsed.row_index == 2:
            raise psycopg.errors.IntegrityError(
                "forced for test: row 2 stage error"
            )
        return original_process(cur, run_id, parsed, existing, counts, source_file)

    monkeypatch.setattr(pipeline_mod, "_process_row", patched_process)

    fixture = fixture_path / "products_m2_minimal.json"
    result = run_pipeline(
        PipelineOptions(input_path=fixture, source=clean_source, batch_size=10)
    )

    # 5 valid rows - 1 forced failure = 4 products upserted
    assert result.counts["products"]["upserted"] == 4
    assert result.counts["row_level_failures"] == 1
    assert result.counts["errors"]["total"] == 1
    # 1/6 = 16.7% failures > 1% threshold (plan §7.5) -> 'failed'
    assert result.status == "failed"
    assert _count_products(clean_source) == 4
    assert _count_crawl_errors(result.run_id) == 1


# --- test: rollback / no partial write ----------------------------------


def test_rollback_no_partial_write(clean_source, fixture_path, monkeypatch):
    """When the batch transaction fails, no rows from the failed batch are
    partially committed. Per-row replay then commits the good rows."""
    from scripts.m2_pipeline import pipeline as pipeline_mod

    original_process = pipeline_mod._process_row

    def patched_process(cur, run_id, parsed, existing, counts, source_file):
        # Force mainboard row (row_index=3) to fail
        if parsed.row_index == 3:
            raise psycopg.errors.IntegrityError("forced for test: row 3")
        return original_process(cur, run_id, parsed, existing, counts, source_file)

    monkeypatch.setattr(pipeline_mod, "_process_row", patched_process)

    fixture = fixture_path / "products_m2_minimal.json"
    result = run_pipeline(
        PipelineOptions(input_path=fixture, source=clean_source, batch_size=10)
    )

    # 5 valid - 1 forced fail = 4 products
    assert result.counts["products"]["upserted"] == 4
    # The failed row's source_url is NOT in products
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM products WHERE source = %s "
                "AND source_url LIKE %s",
                (clean_source, "%test-mainboard%"),
            )
            assert cur.fetchone()["c"] == 0  # mainboard row was forced fail


# --- test: SQL safety (special chars in name) ---------------------------


def test_sql_safety_special_chars(clean_source, fixture_path):
    fixture = fixture_path / "products_m2_minimal.json"
    result = run_pipeline(PipelineOptions(input_path=fixture, source=clean_source))
    assert result.counts["products"]["upserted"] == 5

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM products WHERE source = %s AND sku = %s",
                (clean_source, "SKU-MIN-001"),
            )
            row = cur.fetchone()
            assert row is not None
            # Literal special chars preserved
            assert "Đặc biệt" in row["name"]
            assert '"Pro"' in row["name"]
            assert ";" in row["name"]
            assert "--" in row["name"]
            assert "'quotes'" in row["name"]


# --- test: slug collision (same name, different source_url) -------------


def test_slug_collision(clean_source, fixture_path):
    fixture = fixture_path / "products_m2_minimal.json"
    result = run_pipeline(PipelineOptions(input_path=fixture, source=clean_source))
    assert result.counts["products"]["upserted"] == 5

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT slug FROM products WHERE source = %s "
                "AND sku IN ('SKU-MIN-001', 'SKU-MIN-005') ORDER BY sku",
                (clean_source,),
            )
            slugs = [r["slug"] for r in cur.fetchall()]
            assert len(slugs) == 2
            assert slugs[0] != slugs[1]  # different 8-hex suffixes


# --- test: schema + extensions smoke -----------------------------------


def test_schema_extensions_smoke():
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension")
            exts = {r["extname"] for r in cur.fetchall()}
            assert {"pgcrypto", "unaccent", "vector", "pg_search"}.issubset(exts)

            cur.execute(
                "SELECT count(*) AS c FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' "
                "AND table_name != 'spatial_ref_sys'"
            )
            assert cur.fetchone()["c"] == 12

            cur.execute(
                "SELECT count(*) AS c FROM information_schema.views "
                "WHERE table_schema = 'public' AND table_name = 'product_current_prices'"
            )
            assert cur.fetchone()["c"] == 1
