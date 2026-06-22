"""Small compatibility wrapper around the official Meilisearch Python SDK."""
from __future__ import annotations

import json
import time
from typing import Any

from scripts.m3_search.config import (
    get_meili_host,
    get_meili_master_key,
    get_meili_timeout_seconds,
)


def get_client():
    import meilisearch

    return meilisearch.Client(get_meili_host(), get_meili_master_key())


def get_task_uid(task: Any) -> int | str:
    if task is None:
        raise RuntimeError("Meilisearch task response is empty")
    if isinstance(task, dict):
        for key in ("taskUid", "uid", "updateId", "update_id"):
            if key in task:
                return task[key]
    for key in ("task_uid", "taskUid", "uid", "update_id", "updateId"):
        if hasattr(task, key):
            return getattr(task, key)
    raise RuntimeError(f"Cannot extract Meilisearch task uid from: {task!r}")


def task_succeeded(task: Any) -> bool:
    status = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
    return status in {"succeeded", "success"}


def task_failed(task: Any) -> bool:
    status = task.get("status") if isinstance(task, dict) else getattr(task, "status", None)
    return status in {"failed", "canceled", "cancelled"}


def wait_for_task(client, task: Any, timeout_s: int | None = None):
    uid = get_task_uid(task)
    timeout_s = timeout_s or get_meili_timeout_seconds()
    if hasattr(client, "wait_for_task"):
        return client.wait_for_task(uid, timeout_in_ms=timeout_s * 1000)

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        task_info = client.get_task(uid)
        if task_succeeded(task_info):
            return task_info
        if task_failed(task_info):
            raise RuntimeError(f"Meilisearch task failed: {task_info!r}")
        time.sleep(0.25)
    raise TimeoutError(f"Meilisearch task {uid} did not finish within {timeout_s}s")


def wait_for_tasks(client, tasks: list[Any]) -> None:
    for task in tasks:
        wait_for_task(client, task)


def health_check(client) -> dict:
    if hasattr(client, "health"):
        return client.health()
    return client.get_health()


def index_exists(client, index_name: str) -> bool:
    try:
        client.get_index(index_name)
        return True
    except Exception:
        return False


def ensure_index(client, index_name: str):
    try:
        return client.get_index(index_name)
    except Exception:
        task = client.create_index(index_name, {"primaryKey": "id"})
        wait_for_task(client, task)
        return client.get_index(index_name)


def delete_documents_by_filter(index, filter_expr: str):
    """Delete documents matching a Meilisearch filter expression.

    SDK 0.34.1 signature: `delete_documents(ids=None, *, filter=None)`.
    We pass `filter=` as a keyword; `ids` stays None.
    """
    if not hasattr(index, "delete_documents"):
        raise RuntimeError("Pinned Meilisearch SDK does not support delete_documents")
    try:
        return index.delete_documents(filter=filter_expr)
    except TypeError:
        return index.delete_documents(filter=[filter_expr])


def delete_documents_by_ids(index, ids: list[str]):
    """Delete documents by primary key list.

    SDK 0.34.1's `delete_documents(ids=...)` is deprecated. We use
    `delete_documents(filter=...)` with an `id IN [...]` filter expression
    instead. The `id` values are already Meili-safe (sanitized) so JSON
    encoding is safe.

    Returns the Meili task, or `None` when `ids` is empty (no-op).
    """
    if not ids:
        return None
    filter_expr = f"id IN {json.dumps(ids)}"
    return delete_documents_by_filter(index, filter_expr)


def get_document_count_for_source(index, source: str, quote_filter_value) -> int:
    res = index.search("", {"filter": f"source = {quote_filter_value(source)}", "limit": 0})
    if isinstance(res, dict):
        return int(res.get("estimatedTotalHits") or res.get("nbHits") or 0)
    return int(getattr(res, "estimated_total_hits", 0) or getattr(res, "estimatedTotalHits", 0) or 0)


def get_global_index_documents(client, index_name: str) -> int | None:
    try:
        stats = client.get_all_stats()
    except Exception:
        return None
    if not isinstance(stats, dict):
        return None
    indexes = stats.get("indexes") or {}
    entry = indexes.get(index_name)
    if not isinstance(entry, dict):
        return None
    return int(entry.get("numberOfDocuments") or 0)
