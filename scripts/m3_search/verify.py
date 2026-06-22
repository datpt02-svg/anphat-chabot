"""M3 verification checks for Meilisearch search layer."""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from typing import Any

from scripts.m3_search.db import connect
from scripts.m3_search.documents import count_active_products, sanitize_id
from scripts.m3_search.index_settings import DESIRED_SETTINGS, normalize_settings, settings_match
from scripts.m3_search.meili import (
    ensure_index,
    get_client,
    get_document_count_for_source,
    get_global_index_documents,
    health_check,
    index_exists,
)
from scripts.m3_search.search import FACETS_DEFAULT, quote_filter_value, search_products
from scripts.m3_search.sync import outbox_counts

SQL_SAMPLE_IDS = """
SELECT id FROM products
WHERE source = %s AND status = 'active' AND deleted_at IS NULL
ORDER BY id
LIMIT 5
"""


@dataclass
class VerifyResult:
    checks: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    status: str = "ok"

    def to_dict(self) -> dict:
        return {"checks": self.checks, "details": self.details}


def _get_hit_count(res: dict) -> int:
    return int(res.get("pagination", {}).get("total_hits") or 0)


def _get_global_documents(client, index_name: str) -> int | None:
    return get_global_index_documents(client, index_name)


def verify(source: str, index_name: str) -> VerifyResult:
    result = VerifyResult()

    # DB connection.
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        result.checks["db_connection"] = "PASS"
    except Exception as exc:
        result.status = "failed"
        result.checks["db_connection"] = "FAIL"
        result.details["db_error"] = f"{type(exc).__name__}: {exc}"
        return result

    # Meili connection/index.
    try:
        client = get_client()
        result.details["health"] = health_check(client)
        result.checks["meili_health"] = "PASS"
    except Exception as exc:
        result.status = "failed"
        result.checks["meili_health"] = "FAIL"
        result.details["meili_error"] = f"{type(exc).__name__}: {exc}"
        return result

    if not index_exists(client, index_name):
        result.status = "failed"
        result.checks["index_exists"] = "FAIL"
        result.details["index"] = index_name
        return result
    result.checks["index_exists"] = "PASS"
    index = ensure_index(client, index_name)

    settings = normalize_settings(index.get_settings())
    result.details["settings"] = settings
    result.checks["settings"] = "PASS" if settings_match(index.get_settings()) else "FAIL"

    db_count = count_active_products(source)
    meili_source_count = get_document_count_for_source(index, source, quote_filter_value)
    global_count = _get_global_documents(client, index_name)
    result.details.update({
        "source": source,
        "index": index_name,
        "db_active_products": db_count,
        "meili_source_documents": meili_source_count,
        "meili_global_documents": global_count,
    })
    result.checks["count_match"] = "PASS" if db_count == meili_source_count else "FAIL"

    counts = outbox_counts(source)
    result.details["outbox"] = counts
    result.checks["outbox"] = "PASS" if counts["pending"] == 0 and counts["failed"] == 0 else "FAIL"

    # Sample docs.
    sample_ids = []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_SAMPLE_IDS, (source,))
            sample_ids = [r["id"] for r in cur.fetchall()]
    missing = []
    for pid in sample_ids:
        # Meili doc id is sanitized (no `:`) — see sanitize_id.
        try:
            index.get_document(sanitize_id(pid))
        except Exception:
            missing.append(pid)
    result.details["sample_ids"] = sample_ids
    result.details["missing_sample_ids"] = missing
    result.checks["sample_docs"] = "PASS" if not missing else "FAIL"

    # Search smoke.
    smoke = {}
    for query in ("HP Pavilion", "DDR4", "Core i5", "RTX", "laptop"):
        try:
            smoke[query] = _get_hit_count(search_products(index, query, source, limit=5, facets=FACETS_DEFAULT))
        except Exception:
            smoke[query] = 0
    result.details["smoke"] = smoke
    result.checks["search_smoke"] = "PASS" if all(v >= 1 for v in smoke.values()) else "FAIL"

    # Facets and filters.
    try:
        facet_res = search_products(index, "", source, limit=1, facets=FACETS_DEFAULT)
        facets = facet_res.get("facets") or {}
        result.details["facets"] = facets
        result.checks["facets"] = "PASS" if all(k in facets for k in FACETS_DEFAULT) else "FAIL"
    except Exception as exc:
        result.details["facets_error"] = f"{type(exc).__name__}: {exc}"
        result.checks["facets"] = "FAIL"

    filter_checks = {}
    for name, kwargs in {
        "category": {"filters": {"category": "laptop"}},
        "price": {"filters": {"price_max": 15000000}},
        "ram": {"filters": {"ram_min": 16}},
    }.items():
        try:
            res = search_products(index, "", source, limit=5, facets=[], **kwargs)
            filter_checks[name] = _get_hit_count(res) >= 0
        except Exception:
            filter_checks[name] = False
    result.details["filter_checks"] = filter_checks
    result.checks["filters"] = "PASS" if all(filter_checks.values()) else "FAIL"

    has_fail = any(v == "FAIL" for v in result.checks.values())
    result.status = "failed" if has_fail else "ok"
    return result


def main(argv: list[str]) -> int:
    import argparse
    from scripts.m3_search.config import get_products_index, resolve_source

    parser = argparse.ArgumentParser(description="M3 verify")
    parser.add_argument("--source", default=None)
    parser.add_argument("--index", default=None)
    args = parser.parse_args(argv)
    res = verify(resolve_source(args.source), args.index or get_products_index())
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
