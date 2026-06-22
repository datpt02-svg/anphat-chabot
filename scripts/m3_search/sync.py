"""M3 Meilisearch setup, rebuild, and search_outbox sync."""
from __future__ import annotations

import time
from typing import Any

from scripts.m3_search.db import connect
from scripts.m3_search.documents import (
    count_active_products,
    iter_product_documents,
    load_documents_for_ids,
    sanitize_id,
)
from scripts.m3_search.index_settings import apply_settings
from scripts.m3_search.meili import (
    delete_documents_by_filter,
    delete_documents_by_ids,
    ensure_index,
    get_client,
    get_document_count_for_source,
    health_check,
    wait_for_task,
    wait_for_tasks,
)
from scripts.m3_search.search import quote_filter_value

MAX_ATTEMPTS = 5

SQL_ENQUEUE_ALL = """
INSERT INTO search_outbox (event_type, product_id, payload, status)
SELECT 'product_search_upsert', p.id, '{}'::jsonb, 'pending'
FROM products p
WHERE p.source = %s
  AND p.status = 'active'
  AND p.deleted_at IS NULL
  AND NOT EXISTS (
    SELECT 1 FROM search_outbox so
    WHERE so.product_id = p.id
      AND so.event_type = 'product_search_upsert'
      AND so.status IN ('pending', 'processing')
  )
RETURNING id
"""

SQL_CLAIM_PENDING = """
SELECT so.id, so.event_type, so.product_id, so.payload, so.attempts
FROM search_outbox so
JOIN products p ON p.id = so.product_id
WHERE so.status = 'pending'
  AND p.source = %s
ORDER BY so.created_at ASC
LIMIT %s
FOR UPDATE SKIP LOCKED
"""

SQL_MARK_PROCESSING = """
UPDATE search_outbox SET status = 'processing'
WHERE id = ANY(%s::uuid[])
"""

SQL_MARK_DONE = """
UPDATE search_outbox
SET status = 'done', processed_at = now(), error_message = NULL
WHERE id = ANY(%s::uuid[])
"""

SQL_MARK_FAILED_FINAL = """
UPDATE search_outbox
SET status = 'failed', attempts = attempts + 1, error_message = %s
WHERE id = ANY(%s::uuid[])
"""

SQL_MARK_FAILED_RETRY = """
UPDATE search_outbox
SET status = 'pending', attempts = attempts + 1, error_message = %s
WHERE id = ANY(%s::uuid[])
"""

SQL_MARK_UNSUPPORTED = """
UPDATE search_outbox
SET status = 'failed', attempts = attempts + 1,
    error_message = 'index_rebuild_requested_out_of_scope'
WHERE id = ANY(%s::uuid[])
"""

SQL_REQUEUE_STALE = """
UPDATE search_outbox
SET status = 'pending', error_message = 'requeued_stale'
WHERE status = 'processing'
  AND created_at < now() - (%s * interval '1 minute')
RETURNING id
"""

SQL_CLEAR_OUTBOX_FOR_SOURCE = """
DELETE FROM search_outbox
WHERE product_id IN (SELECT id FROM products WHERE source = %s)
"""

SQL_OUTBOX_COUNTS = """
SELECT
  count(*) FILTER (WHERE so.status = 'pending') AS pending,
  count(*) FILTER (WHERE so.status = 'processing') AS processing,
  count(*) FILTER (WHERE so.status = 'done') AS done,
  count(*) FILTER (WHERE so.status = 'failed') AS failed
FROM search_outbox so
JOIN products p ON p.id = so.product_id
WHERE p.source = %s
"""


def _add_documents(index, docs: list[dict]):
    try:
        return index.add_documents(docs, primary_key="id")
    except TypeError:
        return index.add_documents(docs)


def setup_index(index_name: str) -> dict:
    client = get_client()
    health = health_check(client)
    index = ensure_index(client, index_name)
    settings = apply_settings(index, lambda task: wait_for_task(client, task))
    return {"health": health, "index": index_name, "settings": settings}


def enqueue_all(source: str, index_name: str | None = None) -> dict:
    warning = None
    if index_name:
        try:
            client = get_client()
            index = ensure_index(client, index_name)
            if get_document_count_for_source(index, source, quote_filter_value) == count_active_products(source):
                warning = (
                    "source appears already fully indexed; enqueue-all will create "
                    "redundant upsert events. Use only after incremental DB changes "
                    "or failed sync recovery."
                )
        except Exception:
            warning = None
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_ENQUEUE_ALL, (source,))
            inserted = len(cur.fetchall())
        conn.commit()
    return {"source": source, "enqueued": inserted, "warning": warning}


def claim_pending(source: str, limit: int) -> list[dict]:
    with connect() as conn:
        with conn.transaction():
            with conn.cursor() as cur:
                cur.execute(SQL_CLAIM_PENDING, (source, limit))
                rows = [dict(r) for r in cur.fetchall()]
                ids = [r["id"] for r in rows]
                if ids:
                    cur.execute(SQL_MARK_PROCESSING, (ids,))
        conn.commit()
    return rows


def _mark_done(ids: list[str]) -> None:
    if not ids:
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_MARK_DONE, (ids,))
        conn.commit()


def _mark_failed(events: list[dict], error_message: str) -> None:
    if not events:
        return
    retry_ids = [e["id"] for e in events if int(e.get("attempts") or 0) + 1 < MAX_ATTEMPTS]
    final_ids = [e["id"] for e in events if int(e.get("attempts") or 0) + 1 >= MAX_ATTEMPTS]
    with connect() as conn:
        with conn.cursor() as cur:
            if retry_ids:
                cur.execute(SQL_MARK_FAILED_RETRY, (error_message[:1000], retry_ids))
            if final_ids:
                cur.execute(SQL_MARK_FAILED_FINAL, (error_message[:1000], final_ids))
        conn.commit()


def _mark_unsupported(ids: list[str]) -> None:
    if not ids:
        return
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_MARK_UNSUPPORTED, (ids,))
        conn.commit()


def sync_pending(source: str, index_name: str, limit: int) -> dict:
    client = get_client()
    index = ensure_index(client, index_name)
    events = claim_pending(source, limit)
    if not events:
        return {"source": source, "claimed": 0, "done": 0, "failed": 0, "unsupported": 0}

    unsupported = [e for e in events if e["event_type"] == "index_rebuild_requested"]
    unsupported_ids = [e["id"] for e in unsupported]
    _mark_unsupported(unsupported_ids)

    actionable = [e for e in events if e["event_type"] != "index_rebuild_requested"]
    done_ids: list[str] = []
    failed_events: list[dict] = []

    try:
        upsert_ids = [e["product_id"] for e in actionable if e["event_type"] == "product_search_upsert"]
        delete_ids = [e["product_id"] for e in actionable if e["event_type"] == "product_search_delete"]
        docs = load_documents_for_ids(upsert_ids)
        active_ids = {d["product_id"] for d in docs}
        stale_upsert_ids = [pid for pid in upsert_ids if pid not in active_ids]
        if docs:
            wait_for_task(client, _add_documents(index, docs))
        # Upsert events for now-inactive products delete any stale Meili doc.
        delete_all = delete_ids + stale_upsert_ids
        if delete_all:
            meili_ids = [sanitize_id(pid) for pid in delete_all]
            task = delete_documents_by_ids(index, meili_ids)
            if task is not None:
                wait_for_task(client, task)
        done_ids = [e["id"] for e in actionable]
        _mark_done(done_ids)
    except Exception as exc:
        failed_events = actionable
        _mark_failed(failed_events, f"{type(exc).__name__}: {exc}")

    return {
        "source": source,
        "claimed": len(events),
        "done": len(done_ids),
        "failed": len(failed_events),
        "unsupported": len(unsupported_ids),
    }


def rebuild(source: str, index_name: str, batch_size: int, json_progress: bool = False) -> dict:
    client = get_client()
    index = ensure_index(client, index_name)
    apply_settings(index, lambda task: wait_for_task(client, task))

    active_count = count_active_products(source)
    delete_task = delete_documents_by_filter(index, f"source = {quote_filter_value(source)}")
    wait_for_task(client, delete_task)

    tasks = []
    indexed = 0
    start = time.time()
    for docs in iter_product_documents(source, batch_size):
        if not docs:
            continue
        tasks.append(_add_documents(index, docs))
        indexed += len(docs)
        if json_progress:
            elapsed = max(0.001, time.time() - start)
            print(
                f'M3_PROGRESS {{"stage":"rebuild","indexed":{indexed},'
                f'"total":{active_count},"rate_docs_per_sec":{indexed / elapsed:.2f}}}',
                flush=True,
            )
    wait_for_tasks(client, tasks)

    meili_count = get_document_count_for_source(index, source, quote_filter_value)
    if meili_count != active_count:
        raise RuntimeError(
            f"rebuild count mismatch for {source}: db={active_count} meili={meili_count}"
        )

    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_CLEAR_OUTBOX_FOR_SOURCE, (source,))
            cleared = cur.rowcount
        conn.commit()
    return {
        "source": source,
        "index": index_name,
        "indexed": indexed,
        "db_active_products": active_count,
        "meili_source_documents": meili_count,
        "outbox_cleared": cleared,
    }


def requeue_stale(older_than_minutes: int) -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_REQUEUE_STALE, (older_than_minutes,))
            ids = [str(r["id"]) for r in cur.fetchall()]
        conn.commit()
    return {"requeued": len(ids), "ids": ids}


def outbox_counts(source: str) -> dict:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_OUTBOX_COUNTS, (source,))
            row = cur.fetchone() or {}
            return {k: int(row.get(k) or 0) for k in ("pending", "processing", "done", "failed")}
