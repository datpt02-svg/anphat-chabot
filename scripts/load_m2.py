"""M2 CLI entrypoint.

Subcommands:
- run        Import `products_final.json` into the database.
- verify     Run post-import SQL checks (see scripts/m2_pipeline/verify.py).
- check-db   Verify connection + extensions + schema tables.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.config import (  # noqa: E402
    DEFAULT_BATCH_SIZE,
    DEFAULT_INPUT_PATH,
    DEFAULT_MAX_ROWS,
    get_database_url,
    resolve_source,
)
from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m2_pipeline.pipeline import PipelineOptions, run_pipeline  # noqa: E402


# --- subcommand: run ------------------------------------------------------


def cmd_run(args: argparse.Namespace) -> int:
    opts = PipelineOptions(
        input_path=Path(args.input),
        source=resolve_source(args.source),
        batch_size=args.batch_size,
        max_rows=args.max_rows,
        run_id=args.run_id,
        dry_run=args.dry_run,
        json_progress=args.json_progress,
    )
    try:
        result = run_pipeline(opts)
    except Exception as exc:
        print(f"FATAL: {type(exc).__name__}: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 2

    print(json.dumps({
        "run_id": result.run_id,
        "status": result.status,
        "duration_seconds": round(result.duration_seconds, 3),
        "error_message": result.error_message,
        "counts": result.counts,
    }, ensure_ascii=False, indent=2))

    if result.status == "failed":
        return 1
    return 0


# --- subcommand: verify ---------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    from scripts.m2_pipeline.verify import verify
    res = verify(source=resolve_source(args.source), run_id=args.run_id)
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
    return 0 if res.status == "ok" else 1


# --- subcommand: check-db -------------------------------------------------


def cmd_check_db(args: argparse.Namespace) -> int:
    checks: dict = {}
    details: dict = {}

    # 1. Connection
    try:
        with connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                checks["connection"] = "PASS"
                details["connection"] = "SELECT 1 returned"
    except Exception as exc:
        checks["connection"] = "FAIL"
        details["connection"] = f"{type(exc).__name__}: {exc}"
        print(json.dumps({"checks": checks, "details": details}, indent=2))
        return 1

    # 2. Extensions
    required_exts = {"pgcrypto", "unaccent", "vector", "pg_search"}
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extname FROM pg_extension")
            exts = {r["extname"] for r in cur.fetchall()}
            missing = required_exts - exts
            details["extensions_found"] = sorted(exts)
            if missing:
                checks["extensions"] = "FAIL"
                details["extensions_missing"] = sorted(missing)
            else:
                checks["extensions"] = "PASS"

            # 3. Schema tables
            cur.execute("""
                SELECT count(*) AS c FROM information_schema.tables
                WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
                  AND table_name != 'spatial_ref_sys'
            """)
            table_count = cur.fetchone()["c"]
            details["table_count"] = table_count
            checks["schema_tables"] = "PASS" if table_count == 12 else "FAIL"

            cur.execute("""
                SELECT count(*) AS c FROM information_schema.views
                WHERE table_schema = 'public' AND table_name = 'product_current_prices'
            """)
            view_count = cur.fetchone()["c"]
            details["view_count"] = view_count
            checks["schema_view"] = "PASS" if view_count == 1 else "FAIL"

    has_fail = any(v == "FAIL" for v in checks.values())
    print(json.dumps({"checks": checks, "details": details}, indent=2))
    return 0 if not has_fail else 1


# --- main -----------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="M2 import pipeline CLI")
    sub = parser.add_subparsers(dest="subcommand", required=True)

    # run
    p_run = sub.add_parser("run", help="Run the import pipeline")
    p_run.add_argument("--input", default=DEFAULT_INPUT_PATH)
    p_run.add_argument("--source", default=None,
                       help="crawl_runs.source value (default: anphatpc; "
                            "overrides M2_TEST_SOURCE / M2_SOURCE env)")
    p_run.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    p_run.add_argument("--max-rows", type=int, default=DEFAULT_MAX_ROWS,
                       help="Limit rows read from input. Omit = unlimited. "
                            "0 is rejected.")
    p_run.add_argument("--run-id", default=None,
                       help="Resume an existing crawl_runs.id. Counts OVERWRITE.")
    p_run.add_argument("--dry-run", action="store_true",
                       help="Skip all DB writes; still compute counts.")
    p_run.add_argument("--json-progress", action="store_true",
                       help="Emit M2_PROGRESS lines on stderr.")
    p_run.set_defaults(func=cmd_run)

    # verify
    p_verify = sub.add_parser("verify", help="Run post-import SQL checks")
    p_verify.add_argument("--source", default=None)
    p_verify.add_argument("--run-id", default=None)
    p_verify.set_defaults(func=cmd_verify)

    # check-db
    p_check = sub.add_parser("check-db", help="Verify DB connection + schema")
    p_check.set_defaults(func=cmd_check_db)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # Validate --max-rows
    if hasattr(args, "max_rows") and args.max_rows is not None and args.max_rows <= 0:
        print("ERROR: --max-rows must be > 0 (or omit for unlimited).",
              file=sys.stderr)
        return 2

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
