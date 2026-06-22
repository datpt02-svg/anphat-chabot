"""M3 Meilisearch search layer CLI."""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from typing import Optional

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m3_search.config import (  # noqa: E402
    get_database_url,
    get_products_index,
    get_sync_batch_size,
    resolve_source,
)
from scripts.m3_search.db import connect  # noqa: E402
from scripts.m3_search.fallback import fallback_search  # noqa: E402
from scripts.m3_search.meili import ensure_index, get_client, health_check, index_exists  # noqa: E402
from scripts.m3_search.search import FACETS_DEFAULT, search_products  # noqa: E402
from scripts.m3_search.sync import (  # noqa: E402
    enqueue_all,
    rebuild,
    requeue_stale,
    setup_index,
    sync_pending,
)
from scripts.m3_search.verify import verify  # noqa: E402


# --- commands ---------------------------------------------------------------


def cmd_check(args: argparse.Namespace) -> int:
    checks = {}
    details = {}
    try:
        get_database_url()
        index_name = args.index or get_products_index()
        checks["config"] = "PASS"
    except Exception as exc:
        checks["config"] = "FAIL"
        details["config_error"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps({"checks": checks, "details": details}, ensure_ascii=False, indent=2))
        return 2

    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        checks["db"] = "PASS"
    except Exception as exc:
        checks["db"] = "FAIL"
        details["db_error"] = f"{type(exc).__name__}: {exc}"

    try:
        client = get_client()
        details["health"] = health_check(client)
        checks["meili"] = "PASS"
        checks["index"] = "PASS" if index_exists(client, index_name) else "FAIL"
        if checks["index"] == "FAIL":
            details["index_error"] = "index missing; run setup-index"
    except Exception as exc:
        checks["meili"] = "FAIL"
        details["meili_error"] = f"{type(exc).__name__}: {exc}"

    print(json.dumps({"checks": checks, "details": details}, ensure_ascii=False, indent=2))
    return 0 if all(v == "PASS" for v in checks.values()) else 1


def cmd_setup_index(args: argparse.Namespace) -> int:
    res = setup_index(args.index or get_products_index())
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_enqueue_all(args: argparse.Namespace) -> int:
    source = resolve_source(args.source)
    res = enqueue_all(source, args.index or get_products_index())
    if res.get("warning"):
        print(f"WARNING: {res['warning']}", file=sys.stderr)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    res = sync_pending(resolve_source(args.source), args.index or get_products_index(), args.limit)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_rebuild(args: argparse.Namespace) -> int:
    res = rebuild(
        resolve_source(args.source),
        args.index or get_products_index(),
        args.batch_size,
        json_progress=args.json_progress,
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    res = verify(resolve_source(args.source), args.index or get_products_index())
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.status == "ok" else 1


def cmd_search(args: argparse.Namespace) -> int:
    source = resolve_source(args.source)
    if args.fallback:
        res = fallback_search(args.q or "", source, page=args.page, limit=args.limit)
    else:
        client = get_client()
        index = ensure_index(client, args.index or get_products_index())
        filters = {}
        for name in (
            "category", "subcategory", "brand", "stock_status", "price_min", "price_max",
            "ram_min", "ram_max", "storage_min", "storage_max", "gpu_model", "cpu_model",
            "socket", "screen_min", "refresh_rate_min",
        ):
            value = getattr(args, name, None)
            if value is not None:
                filters[name] = value
        res = search_products(
            index,
            args.q or "",
            source,
            filters=filters,
            sort=args.sort,
            page=args.page,
            limit=args.limit,
            facets=FACETS_DEFAULT if args.facets else [],
        )
    print(json.dumps(res, ensure_ascii=False, indent=2 if args.json else None))
    return 0


def cmd_requeue_stale(args: argparse.Namespace) -> int:
    res = requeue_stale(args.older_than_minutes)
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


# --- parser -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M3 Meilisearch search layer CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    p = sub.add_parser("check", help="Check DB + Meili + index")
    p.add_argument("--index", default=None)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("setup-index", help="Create index and apply settings")
    p.add_argument("--index", default=None)
    p.set_defaults(func=cmd_setup_index)

    p = sub.add_parser("enqueue-all", help="Backfill search_outbox upsert events")
    p.add_argument("--source", default=None)
    p.add_argument("--index", default=None)
    p.set_defaults(func=cmd_enqueue_all)

    p = sub.add_parser("sync", help="Process pending search_outbox events for one source")
    p.add_argument("--source", default=None)
    p.add_argument("--index", default=None)
    p.add_argument("--limit", type=int, default=get_sync_batch_size())
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("rebuild", help="Delete-by-source then rebuild Meili docs")
    p.add_argument("--source", default=None)
    p.add_argument("--index", default=None)
    p.add_argument("--batch-size", type=int, default=get_sync_batch_size())
    p.add_argument("--json-progress", action="store_true")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("verify", help="Verify Meili index against PostgreSQL")
    p.add_argument("--source", default=None)
    p.add_argument("--index", default=None)
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("search", help="Run search smoke")
    p.add_argument("--q", default="")
    p.add_argument("--source", default=None)
    p.add_argument("--index", default=None)
    p.add_argument("--page", type=int, default=1)
    p.add_argument("--limit", type=int, default=24)
    p.add_argument("--sort", default="relevance")
    p.add_argument("--json", action="store_true")
    p.add_argument("--fallback", action="store_true")
    p.add_argument("--facets", action="store_true")
    p.add_argument("--category", default=None)
    p.add_argument("--subcategory", default=None)
    p.add_argument("--brand", default=None)
    p.add_argument("--stock-status", dest="stock_status", default=None)
    p.add_argument("--price-min", dest="price_min", type=float, default=None)
    p.add_argument("--price-max", dest="price_max", type=float, default=None)
    p.add_argument("--ram-min", dest="ram_min", type=float, default=None)
    p.add_argument("--ram-max", dest="ram_max", type=float, default=None)
    p.add_argument("--storage-min", dest="storage_min", type=float, default=None)
    p.add_argument("--storage-max", dest="storage_max", type=float, default=None)
    p.add_argument("--gpu-model", dest="gpu_model", default=None)
    p.add_argument("--cpu-model", dest="cpu_model", default=None)
    p.add_argument("--socket", default=None)
    p.add_argument("--screen-min", dest="screen_min", type=float, default=None)
    p.add_argument("--refresh-rate-min", dest="refresh_rate_min", type=float, default=None)
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("requeue-stale", help="Move stale processing events back to pending")
    p.add_argument("--older-than-minutes", type=int, default=15)
    p.set_defaults(func=cmd_requeue_stale)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if hasattr(args, "limit") and args.limit is not None and args.limit <= 0:
        print("ERROR: --limit must be > 0", file=sys.stderr)
        return 2
    if hasattr(args, "batch_size") and args.batch_size is not None and args.batch_size <= 0:
        print("ERROR: --batch-size must be > 0", file=sys.stderr)
        return 2
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
