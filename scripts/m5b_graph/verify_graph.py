"""M5b graph verification CLI.

Sanity checks for graph_nodes + graph_edges:
- Node count matches active products count
- Edge count by relation
- Self-loops / dangling references
- Sample neighbors for a random product
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m5b_graph.config import get_default_source  # noqa: E402

logger = logging.getLogger("scripts.m5b_graph.verify")

_SQL_STATS = """
SELECT
    (SELECT count(*) FROM graph_nodes) AS nodes_total,
    (SELECT count(*) FROM graph_nodes WHERE type = 'product') AS nodes_product,
    (SELECT count(*) FROM graph_edges) AS edges_total,
    (SELECT count(*) FROM graph_edges WHERE relation = 'compatible_with') AS edges_compat,
    (SELECT count(*) FROM graph_edges WHERE relation = 'substitutes') AS edges_sub,
    (SELECT count(*) FROM graph_edges WHERE src = dst) AS self_loops
"""

_SQL_DANGLING = """
SELECT e.src, e.relation, e.dst
FROM graph_edges e
LEFT JOIN graph_nodes a ON a.id = e.src
LEFT JOIN graph_nodes b ON b.id = e.dst
WHERE a.id IS NULL OR b.id IS NULL
LIMIT 20
"""

_SQL_ACTIVE_PRODUCTS = """
SELECT count(*) AS c
FROM products
WHERE status = 'active' AND deleted_at IS NULL AND source = %s
"""


def verify(source: str) -> dict[str, Any]:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(_SQL_STATS)
            stats = dict(cur.fetchone())
            cur.execute(_SQL_DANGLING)
            dangling = [dict(r) for r in cur.fetchall()]
            cur.execute(_SQL_ACTIVE_PRODUCTS, (source,))
            products_count = int(cur.fetchone()["c"])
    stats["dangling_edges"] = len(dangling)
    stats["active_products"] = products_count
    stats["coverage_pct"] = (
        round(100.0 * stats["nodes_product"] / products_count, 1) if products_count else 0.0
    )
    if dangling:
        stats["dangling_sample"] = dangling[:5]
    return stats


def cmd_verify(args: argparse.Namespace) -> int:
    source = args.source or get_default_source()
    stats = verify(source)
    print(json.dumps(stats, indent=2, default=str))
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="M5b graph verification")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_v = sub.add_parser("verify", help="Sanity checks for graph tables")
    p_v.add_argument("--source", default=None)
    p_v.set_defaults(func=cmd_verify)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
