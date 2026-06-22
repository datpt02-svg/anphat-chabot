"""Pytest config + isolation fixtures for M2/M3 integration tests.

Each test gets its own `source` value: f"anphatpc_test_{uuid8}". The pipeline
reads it via M2_TEST_SOURCE env var (per `config.resolve_source` contract).
The teardown deletes all rows under that source so tests are isolated.

M3 tests also get Meili document cleanup for the same source. The cleanup
is best-effort: failures are logged but do not fail the test, unless the
test is marked `requires_meili` (then a Meili cleanup failure does fail).
"""
from __future__ import annotations

import asyncio
import os
import sys
import uuid
from pathlib import Path

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import psycopg  # noqa: E402
import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

# Order matters: outbox first (FK target), then chunks/specs/prices, then products.
# `products` has ON DELETE CASCADE on `search_outbox.product_id`, but we keep
# the explicit DELETE for readability and to clear events before products.
CLEANUP_SQL = [
    "DELETE FROM search_outbox WHERE product_id IN (SELECT id FROM products WHERE source = %(src)s)",
    "DELETE FROM raw_data WHERE source = %(src)s",
    "DELETE FROM crawl_errors WHERE run_id IN (SELECT id FROM crawl_runs WHERE source = %(src)s)",
    "DELETE FROM product_chunks WHERE product_id IN (SELECT id FROM products WHERE source = %(src)s)",
    "DELETE FROM product_spec_values WHERE product_id IN (SELECT id FROM products WHERE source = %(src)s)",
    "DELETE FROM product_prices WHERE product_id IN (SELECT id FROM products WHERE source = %(src)s)",
    "DELETE FROM product_specs WHERE product_id IN (SELECT id FROM products WHERE source = %(src)s)",
    "DELETE FROM products WHERE source = %(src)s",
    "DELETE FROM crawl_runs WHERE source = %(src)s",
]


def _cleanup_source(source: str) -> None:
    # Use autocommit: each DELETE is its own transaction. If one fails (e.g.
    # constraint violation), the rest still run. In a regular transaction, a
    # single failure aborts the whole transaction and subsequent DELETEs are
    # skipped, leaving products/source_urls behind that block later tests via
    # products_source_url_key.
    with connect(autocommit=True) as conn:
        with conn.cursor() as cur:
            for sql in CLEANUP_SQL:
                try:
                    cur.execute(sql, {"src": source})
                except psycopg.Error as exc:
                    print(
                        f"cleanup DELETE failed for {source!r}: {exc}",
                        file=sys.stderr,
                    )


def _cleanup_meili_for_source(source: str, require_meili: bool) -> None:
    """Best-effort: delete Meili docs for the test source and wait for task.

    Failures are swallowed unless `require_meili` is True (test opted in via
    `requires_meili` marker). Meili env must be present; otherwise skip.
    """
    if not (os.environ.get("MEILI_HOST") and os.environ.get("MEILI_MASTER_KEY")):
        return
    try:
        from scripts.m3_search.config import get_products_index
        from scripts.m3_search.meili import (
            delete_documents_by_filter,
            ensure_index,
            get_client,
            wait_for_task,
        )
        from scripts.m3_search.search import quote_filter_value
    except Exception as exc:
        print(f"meili cleanup skipped: import failed: {exc}", file=sys.stderr)
        return
    try:
        client = get_client()
        index_name = get_products_index()
        index = ensure_index(client, index_name)
        task = delete_documents_by_filter(index, f"source = {quote_filter_value(source)}")
        wait_for_task(client, task)
    except Exception as exc:
        msg = f"meili cleanup failed for {source!r}: {type(exc).__name__}: {exc}"
        if require_meili:
            pytest.fail(msg)
        print(msg, file=sys.stderr)


@pytest.fixture
def clean_source(request):
    """Yield a unique source string for one test; clean up by source on teardown.

    Also sets M2_TEST_SOURCE, M3_TESTING=1, and M3_TEST_SOURCE so M3 source
    resolution honors the test source.
    """
    source = f"anphatpc_test_{uuid.uuid4().hex[:8]}"
    os.environ["M2_TEST_SOURCE"] = source
    os.environ["M3_TESTING"] = "1"
    os.environ["M3_TEST_SOURCE"] = source
    require_meili = "requires_meili" in request.keywords
    try:
        yield source
    finally:
        try:
            _cleanup_source(source)
        finally:
            _cleanup_meili_for_source(source, require_meili)
            os.environ.pop("M2_TEST_SOURCE", None)
            os.environ.pop("M3_TEST_SOURCE", None)
            os.environ.pop("M3_TESTING", None)


@pytest.fixture
def fixture_path():
    """Return the fixtures directory path."""
    return FIXTURES_DIR


def pytest_collection_modifyitems(config, items):
    """Auto-mark tests that need DB with `integration` and Meili with `requires_meili`.

    M3 unit tests (documents, filters, settings) need neither and stay plain pytest tests.
    """
    db_marker = pytest.mark.integration
    meili_marker = pytest.mark.requires_meili
    for item in items:
        path = str(item.fspath)
        if "test_pipeline" in path or "test_m3_sync" in path or "test_m3_search" in path:
            item.add_marker(db_marker)
        if "test_m3_sync" in path or "test_m3_search" in path:
            item.add_marker(meili_marker)
