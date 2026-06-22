"""Integration tests for M3 sync (DB + Meili required).

Run with:
    uv run pytest -q tests/test_m3_sync.py -m integration
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m2_pipeline.pipeline import PipelineOptions, run_pipeline  # noqa: E402
from scripts.m3_search.config import (  # noqa: E402
    get_products_index,
    get_sync_batch_size,
)
from scripts.m3_search.db import connect as connect_m3  # noqa: E402
from scripts.m3_search.documents import count_active_products  # noqa: E402
from scripts.m3_search.meili import (  # noqa: E402
    delete_documents_by_filter,
    delete_documents_by_ids,
    ensure_index,
    get_client,
    get_document_count_for_source,
    wait_for_task,
)
from scripts.m3_search.documents import sanitize_id  # noqa: E402
from scripts.m3_search.search import quote_filter_value  # noqa: E402
from scripts.m3_search.sync import (  # noqa: E402
    enqueue_all,
    rebuild,
    requeue_stale,
    setup_index,
    sync_pending,
)


pytestmark = [pytest.mark.integration, pytest.mark.requires_meili]


INDEX_NAME = get_products_index()


# --- helpers --------------------------------------------------------------


def _import_fixture(clean_source: str, fixture_path: Path) -> int:
    fixture = fixture_path / "products_m3_search.json"
    rows = json.loads(fixture.read_text(encoding="utf-8"))
    result = run_pipeline(
        PipelineOptions(input_path=fixture, source=clean_source, dry_run=False)
    )
    assert result.status == "done", f"pipeline status={result.status}"
    return sum(1 for r in rows if r.get("name"))


def _product_ids(clean_source: str) -> list[str]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM products WHERE source = %s AND status = 'active' AND deleted_at IS NULL ORDER BY id",
                (clean_source,),
            )
            return [r["id"] for r in cur.fetchall()]


def _wait_for_indexing(clean_source: str) -> None:
    """Best-effort: poll until Meili source count == DB active count or timeout."""
    import time
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    target = count_active_products(clean_source)
    deadline = time.time() + 30
    while time.time() < deadline:
        got = get_document_count_for_source(index, clean_source, quote_filter_value)
        if got == target:
            return
        time.sleep(0.5)


def _wait_for_meili_count(clean_source: str, target: int, timeout_s: int = 30) -> int:
    """Poll Meili source count until it equals `target` or timeout. Returns last value."""
    import time
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    deadline = time.time() + timeout_s
    last = -1
    while time.time() < deadline:
        last = get_document_count_for_source(index, clean_source, quote_filter_value)
        if last == target:
            return last
        time.sleep(0.5)
    return last


def _wait_for_doc_deleted(index, meili_id: str, timeout_s: int = 30) -> bool:
    """Poll `index.get_document(meili_id)` until it raises or timeout."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            index.get_document(meili_id)
        except Exception:
            return True
        time.sleep(0.5)
    return False


# --- fixture import + setup ----------------------------------------------


def test_setup_index_creates_index_with_settings(clean_source, fixture_path):
    imported = _import_fixture(clean_source, fixture_path)
    assert imported == 8

    res = setup_index(INDEX_NAME)
    assert res["settings"]["status"] in {"skipped", "updated"}


def test_enqueue_all_creates_pending_events(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    res = enqueue_all(clean_source, INDEX_NAME)
    assert res["enqueued"] == 8


def test_sync_processes_events_to_done(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    res = sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    assert res["claimed"] == 8
    assert res["done"] == 8
    assert res["failed"] == 0
    assert res["unsupported"] == 0

    # After sync, all events for this source should be done.
    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM search_outbox so "
                "JOIN products p ON p.id = so.product_id "
                "WHERE p.source = %s AND so.status = 'pending'",
                (clean_source,),
            )
            assert cur.fetchone()["c"] == 0


def test_meili_source_count_matches_db_active(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    expected = count_active_products(clean_source)
    assert expected == 8
    actual = get_document_count_for_source(index, clean_source, quote_filter_value)
    assert actual == expected


# --- update flow ----------------------------------------------------------


def test_update_product_price_then_upsert_changes_meili_doc(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    ids = _product_ids(clean_source)
    target_id = ids[0]
    meili_id = sanitize_id(target_id)

    before = index.get_document(meili_id)
    assert before is not None
    before_price = before.get("price_vnd") if isinstance(before, dict) else getattr(before, "price_vnd", None)
    assert before_price is not None

    # `product_current_prices` is a view over `product_prices`; the document
    # builder prefers that view's price. So we append a fresh price row to
    # `product_prices` and let the view surface it.
    new_price = 8888888
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO product_prices (product_id, price_vnd, sale_price_vnd, "
                "list_price_vnd, stock_status, price_hash, captured_at) "
                "VALUES (%s, %s, %s, %s, 'in_stock', %s, now())",
                (target_id, new_price, new_price, new_price, f"m3-test-{target_id}"),
            )
        conn.commit()

    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    after = index.get_document(meili_id)
    after_price = after.get("price_vnd") if isinstance(after, dict) else getattr(after, "price_vnd", None)
    assert after_price == new_price


# --- delete flow ----------------------------------------------------------


def test_status_deleted_then_delete_event_removes_meili_doc(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    ids = _product_ids(clean_source)
    target_id = ids[0]
    meili_id = sanitize_id(target_id)

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE products SET status = 'deleted', deleted_at = now() WHERE id = %s",
                (target_id,),
            )
        conn.commit()

    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO search_outbox (event_type, product_id, payload, status) "
                "VALUES ('product_search_delete', %s, '{}'::jsonb, 'pending')",
                (target_id,),
            )
        conn.commit()

    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)
    assert _wait_for_doc_deleted(index, meili_id) is True


# --- failure / requeue ----------------------------------------------------


def test_requeue_stale_returns_processing_events(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)

    ids = _product_ids(clean_source)
    with connect_m3() as conn:
        with conn.cursor() as cur:
            for pid in ids[:3]:
                cur.execute(
                    "INSERT INTO search_outbox (event_type, product_id, payload, status, created_at) "
                    "VALUES ('product_search_upsert', %s, '{}'::jsonb, 'processing', "
                    "now() - interval '30 minutes')",
                    (pid,),
                )
        conn.commit()

    res = requeue_stale(15)
    assert res["requeued"] >= 3

    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM search_outbox so "
                "JOIN products p ON p.id = so.product_id "
                "WHERE p.source = %s AND so.status = 'pending'",
                (clean_source,),
            )
            assert cur.fetchone()["c"] >= 3


def test_unsupported_event_type_marked_failed(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)

    pid = _product_ids(clean_source)[0]
    payload = json.dumps({"source": clean_source})
    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO search_outbox (event_type, product_id, payload, status) "
                "VALUES ('index_rebuild_requested', %s, %s::jsonb, 'pending')",
                (pid, payload),
            )
        conn.commit()

    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())

    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT status, error_message FROM search_outbox "
                "WHERE product_id = %s AND event_type = 'index_rebuild_requested'",
                (pid,),
            )
            row = cur.fetchone()
            assert row["status"] == "failed"
            assert row["error_message"] == "index_rebuild_requested_out_of_scope"


# --- rebuild --------------------------------------------------------------


def test_rebuild_deletes_stale_docs_before_re_adding(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    # Inject a fake stale doc directly into Meili for this source.
    fake_doc = {
        "id": f"{clean_source}_stale_hash",
        "product_id": f"{clean_source}:stale_hash",
        "source": clean_source,
        "name": "STALE PRODUCT",
        "category": "laptop",
        "price_vnd": 1,
        "stock_status": "in_stock",
        "status": "active",
    }
    task = index.add_documents([fake_doc], primary_key="id")
    wait_for_task(client, task)
    assert get_document_count_for_source(index, clean_source, quote_filter_value) == 9

    res = rebuild(clean_source, INDEX_NAME, get_sync_batch_size())
    assert res["indexed"] == 8
    assert res["db_active_products"] == 8
    assert res["meili_source_documents"] == 8
    assert res["outbox_cleared"] >= 0

    # Stale doc should be gone.
    assert get_document_count_for_source(index, clean_source, quote_filter_value) == 8


def test_rebuild_idempotent(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    res1 = rebuild(clean_source, INDEX_NAME, get_sync_batch_size())
    res2 = rebuild(clean_source, INDEX_NAME, get_sync_batch_size())
    assert res1["indexed"] == res2["indexed"] == 8
    assert res1["db_active_products"] == res2["db_active_products"] == 8
    assert res1["meili_source_documents"] == res2["meili_source_documents"] == 8


def test_rebuild_clears_outbox_events(clean_source, fixture_path):
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)

    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM search_outbox so "
                "JOIN products p ON p.id = so.product_id "
                "WHERE p.source = %s",
                (clean_source,),
            )
            assert cur.fetchone()["c"] >= 8

    rebuild(clean_source, INDEX_NAME, get_sync_batch_size())

    with connect_m3() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS c FROM search_outbox so "
                "JOIN products p ON p.id = so.product_id "
                "WHERE p.source = %s",
                (clean_source,),
            )
            assert cur.fetchone()["c"] == 0


# --- delete_documents_by_ids helper ---------------------------------------


def test_delete_documents_by_ids_removes_specific_docs(clean_source, fixture_path):
    """Exercises the `id IN [...]` filter-based delete path used by sync."""
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    enqueue_all(clean_source, INDEX_NAME)
    sync_pending(clean_source, INDEX_NAME, get_sync_batch_size())
    _wait_for_indexing(clean_source)

    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    assert get_document_count_for_source(index, clean_source, quote_filter_value) == 8

    ids = _product_ids(clean_source)
    target_meili_ids = [sanitize_id(pid) for pid in ids[:3]]

    task = delete_documents_by_ids(index, target_meili_ids)
    assert task is not None
    wait_for_task(client, task)
    # Wait for Meili indexing to reflect the delete (count goes 8 -> 5).
    assert _wait_for_meili_count(clean_source, 5) == 5

    # The remaining 5 should all still be there.
    for pid in ids[3:]:
        meili_id = sanitize_id(pid)
        try:
            index.get_document(meili_id)
        except Exception as exc:
            pytest.fail(f"expected {meili_id} to remain, got: {exc}")


def test_delete_documents_by_ids_empty_list_is_noop(clean_source, fixture_path):
    """Empty id list should not call Meili and should not raise."""
    _import_fixture(clean_source, fixture_path)
    setup_index(INDEX_NAME)
    client = get_client()
    index = ensure_index(client, INDEX_NAME)
    assert delete_documents_by_ids(index, []) is None
