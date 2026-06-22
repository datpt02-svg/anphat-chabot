"""M2 verification: post-import SQL checks per plan §8.

Run selection: auto-pick latest crawl_runs for --source, or use --run-id.
All checks are SELECT-only, no data mutation.

Exits 0 if all checks PASS, 1 if any FAIL.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any, Optional

from scripts.m2_pipeline.db import connect


# --- SQL checks -----------------------------------------------------------


SQL_FIND_LATEST_RUN = """
-- Prefer a completed run (`done` or `partial`) over an unfinished `running`
-- row. Falls back to any run for that source if none are completed.
SELECT id, started_at, finished_at, status
FROM crawl_runs
WHERE source = %s
  AND status IN ('done', 'partial')
ORDER BY started_at DESC
LIMIT 1
"""


SQL_SANITY_COUNTS = """
SELECT
  (SELECT count(*) FROM products WHERE source = %s) AS products,
  (SELECT count(*) FROM product_specs) AS specs,
  (SELECT count(*) FROM product_spec_values) AS spec_values,
  (SELECT count(*) FROM product_prices
     WHERE crawl_run_id = %s) AS prices_this_run,
  (SELECT count(*) FROM product_chunks) AS chunks,
  (SELECT count(*) FROM raw_data WHERE run_id = %s) AS raw_this_run
"""


SQL_REferential_INTEGRITY = """
SELECT
  (SELECT count(*) FROM product_specs ps
    WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = ps.product_id)) AS orphan_specs,
  (SELECT count(*) FROM product_spec_values sv
    WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = sv.product_id)) AS orphan_spec_values,
  (SELECT count(*) FROM product_prices pp
    WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = pp.product_id)) AS orphan_prices,
  (SELECT count(*) FROM product_chunks pc
    WHERE NOT EXISTS (SELECT 1 FROM products p WHERE p.id = pc.product_id)) AS orphan_chunks
"""


SQL_SPEC_VALUE_CONSISTENCY = """
SELECT ps.product_id, ps.cpu_model, sv.spec_value
FROM product_specs ps
JOIN product_spec_values sv
  ON sv.product_id = ps.product_id AND sv.spec_key = 'cpu_model'
WHERE ps.cpu_model IS NOT NULL AND sv.spec_value IS DISTINCT FROM ps.cpu_model
LIMIT 5
"""


SQL_PRICE_DEDUP = """
SELECT product_id, count(*) AS dupes
FROM product_prices
GROUP BY product_id, price_hash
HAVING count(*) > 1
LIMIT 5
"""


SQL_CHUNK_DEDUP = """
SELECT product_id, chunk_type, chunk_index, count(*) AS dupes
FROM product_chunks
GROUP BY product_id, chunk_type, chunk_index, content_hash
HAVING count(*) > 1
LIMIT 5
"""


SQL_COVERAGE = """
SELECT
  count(*) FILTER (WHERE price_vnd IS NULL) AS no_price,
  count(*) FILTER (WHERE stock_status IS NULL) AS no_stock,
  count(*) FILTER (WHERE raw_specs = '{}'::jsonb) AS empty_raw_specs,
  count(*) FILTER (WHERE description IS NULL OR description = '') AS no_description
FROM products
WHERE source = %s
"""


SQL_SCHEMA_COLUMNS = """
SELECT column_name FROM information_schema.columns
WHERE table_schema = 'public' AND table_name = 'products'
  AND column_name IN ('id', 'source_url', 'slug', 'name', 'category', 'price_vnd')
"""


SQL_BM25_INDEX = """
SELECT indexname FROM pg_indexes
WHERE tablename = 'product_chunks' AND indexdef ILIKE '%USING bm25%'
"""


SQL_SLUG_LENGTH = """
SELECT
  max(length(slug)) AS max_slug_len,
  count(*) FILTER (WHERE slug IS NULL) AS null_slug_count,
  count(*) FILTER (WHERE length(slug) > 255) AS over_255_count
FROM products
WHERE source = %s
"""


SQL_BM25_SMOKE_TPL = """
SELECT id, product_id, chunk_type FROM product_chunks
WHERE content @@@ %s
LIMIT 5
"""


# --- helpers --------------------------------------------------------------


def _cur_dict(conn):
    return conn.cursor()


def _select_latest_run(source: str) -> Optional[dict]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_FIND_LATEST_RUN, (source,))
            return cur.fetchone()


# --- main entrypoint ------------------------------------------------------


@dataclass
class VerifyResult:
    checks: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    status: str = "ok"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "details": self.details}


def verify(source: str, run_id: Optional[str] = None) -> VerifyResult:
    """Run all §8 checks. Returns a VerifyResult. Caller may print JSON."""
    result = VerifyResult()

    # Resolve run_id
    if run_id is None:
        latest = _select_latest_run(source)
        if latest is None:
            result.status = "failed"
            result.checks["run_selection"] = "FAIL"
            result.details["error"] = f"no crawl_runs found for source={source}"
            return result
        run_id = str(latest["id"])
        result.details["selected_run_id"] = run_id
        result.details["run_started_at"] = str(latest["started_at"])
        result.details["run_status"] = latest["status"]
    else:
        result.details["selected_run_id"] = run_id

    with connect() as conn:
        with conn.cursor() as cur:

            # 8.1 sanity counts (3 placeholders: source, run_id x2)
            cur.execute(SQL_SANITY_COUNTS, (source, run_id, run_id))
            row = cur.fetchone()
            counts = dict(row)
            result.details.update({
                "products": counts["products"],
                "specs": counts["specs"],
                "spec_values": counts["spec_values"],
                "prices_this_run": counts["prices_this_run"],
                "chunks": counts["chunks"],
                "raw_this_run": counts["raw_this_run"],
            })
            if counts["products"] > 0 and counts["products"] == counts["specs"]:
                result.checks["row_counts"] = "PASS"
            else:
                result.checks["row_counts"] = "FAIL"

            # 8.3 referential integrity
            cur.execute(SQL_REferential_INTEGRITY)
            ri = dict(cur.fetchone())
            result.details.update({
                "orphan_specs": ri["orphan_specs"],
                "orphan_spec_values": ri["orphan_spec_values"],
                "orphan_prices": ri["orphan_prices"],
                "orphan_chunks": ri["orphan_chunks"],
            })
            if all(v == 0 for v in ri.values()):
                result.checks["fk"] = "PASS"
            else:
                result.checks["fk"] = "FAIL"

            # 8.4 spec value consistency (cpu_model only as a sample)
            cur.execute(SQL_SPEC_VALUE_CONSISTENCY)
            wide_mismatch = len(cur.fetchall())
            result.details["wide_vs_jsonb_mismatch"] = wide_mismatch
            if wide_mismatch == 0:
                result.checks["spec_consistency"] = "PASS"
            else:
                result.checks["spec_consistency"] = "FAIL"

            # 8.5 price dedup
            cur.execute(SQL_PRICE_DEDUP)
            price_dupes = len(cur.fetchall())
            result.details["price_hash_dupes"] = price_dupes
            if price_dupes == 0:
                result.checks["dedup_prices"] = "PASS"
            else:
                result.checks["dedup_prices"] = "FAIL"

            # 8.6 chunk content_hash
            cur.execute(SQL_CHUNK_DEDUP)
            chunk_dupes = len(cur.fetchall())
            result.details["chunk_content_dupes"] = chunk_dupes
            if chunk_dupes == 0:
                result.checks["dedup_chunks"] = "PASS"
            else:
                result.checks["dedup_chunks"] = "FAIL"

            # 8.7 coverage
            cur.execute(SQL_COVERAGE, (source,))
            cov = dict(cur.fetchone())
            result.details.update({
                "no_price": cov["no_price"],
                "no_stock": cov["no_stock"],
                "empty_raw_specs": cov["empty_raw_specs"],
                "no_description": cov["no_description"],
            })
            result.checks["coverage"] = "INFO"  # informational only

            # 8.8 BM25 smoke
            smoke = {}
            for query in ("HP Pavilion", "DDR4", "Core i5", "RTX"):
                cur.execute(SQL_BM25_SMOKE_TPL, (query,))
                smoke[query] = len(cur.fetchall())
            result.details["bm25_smoke_hits"] = smoke
            if all(v >= 1 for v in smoke.values()):
                result.checks["bm25"] = "PASS"
            else:
                result.checks["bm25"] = "FAIL"

            # 8.9 schema column check
            cur.execute(SQL_SCHEMA_COLUMNS)
            cols = {r["column_name"] for r in cur.fetchall()}
            result.details["wide_columns_present"] = len(cols)
            if len(cols) == 6:
                result.checks["schema_columns"] = "PASS"
            else:
                result.checks["schema_columns"] = "FAIL"

            cur.execute(SQL_BM25_INDEX)
            bm25 = cur.fetchall()
            result.details["bm25_index_present"] = bool(bm25)
            if bm25:
                result.checks["bm25_index"] = "PASS"
            else:
                result.checks["bm25_index"] = "FAIL"

            # 8.10 slug length check
            cur.execute(SQL_SLUG_LENGTH, (source,))
            sl = dict(cur.fetchone())
            result.details.update({
                "max_slug_len": sl["max_slug_len"],
                "null_slug_count": sl["null_slug_count"],
                "over_255_count": sl["over_255_count"],
            })
            if (sl["max_slug_len"] is not None and sl["max_slug_len"] <= 255
                    and sl["null_slug_count"] == 0 and sl["over_255_count"] == 0):
                result.checks["slug_length"] = "PASS"
            else:
                result.checks["slug_length"] = "FAIL"

    # Aggregate
    has_fail = any(v == "FAIL" for v in result.checks.values())
    result.status = "failed" if has_fail else "ok"
    return result


def main(argv: list[str]) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="M2 verify")
    parser.add_argument("--source", required=True)
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    res = verify(args.source, args.run_id)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
