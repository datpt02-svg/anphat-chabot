"""M5b graph population script.

Populates `graph_nodes` + `graph_edges` with:
- All active products as nodes
- CPU <-> Mobo edges (same socket, `compatible_with`)
- RAM <-> Mobo edges (same ram_type, `compatible_with`)
- Substitute edges (price delta 20%, `substitutes`)

Idempotent via `uq_graph_edges_src_relation_dst_hash`.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from scripts.m2_pipeline.db import connect  # noqa: E402
from scripts.m5b_graph.config import (  # noqa: E402
    PRICE_DELTA_DEFAULT,
    get_database_url,
    get_default_source,
)

logger = logging.getLogger("scripts.m5b_graph")

_SQL_PRODUCTS = """
SELECT p.id, p.name, p.category, p.brand
FROM products p
WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
"""

_SQL_CPUS = """
SELECT p.id, ps.socket
FROM products p
JOIN product_specs ps ON ps.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
  AND ps.cpu_cores IS NOT NULL AND ps.socket IS NOT NULL
"""

_SQL_MOBOS = """
SELECT p.id, ps.socket, ps.ram_type
FROM products p
JOIN product_specs ps ON ps.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
  AND ps.form_factor IS NOT NULL AND ps.cpu_cores IS NULL
"""

_SQL_RAMS = """
SELECT p.id, ps.ram_type
FROM products p
JOIN product_specs ps ON ps.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
  AND ps.ram_gb IS NOT NULL AND ps.psu_wattage_w IS NULL AND ps.cpu_cores IS NULL
  AND ps.ram_type IS NOT NULL
"""

_SQL_CATEGORY_PRICES = """
SELECT p.id, COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd
FROM products p
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
  AND COALESCE(cp.price_vnd, p.price_vnd) IS NOT NULL
"""

_SQL_HAS_CPU_CORES = "ps.cpu_cores IS NOT NULL"
_SQL_HAS_GPU = "ps.gpu_model IS NOT NULL"
_SQL_IS_RAM = "ps.ram_gb IS NOT NULL AND ps.psu_wattage_w IS NULL AND ps.cpu_cores IS NULL"
_SQL_IS_STORAGE = "ps.storage_gb IS NOT NULL AND ps.cpu_cores IS NULL AND ps.gpu_model IS NULL"
_SQL_IS_PSU = "ps.psu_wattage_w IS NOT NULL"


def _category_predicate(category: str) -> tuple[str, str]:
    table = (
        "products p JOIN product_specs ps ON ps.product_id = p.id "
        "LEFT JOIN product_current_prices cp ON cp.product_id = p.id"
    )
    if category == "cpu":
        return table, _SQL_HAS_CPU_CORES
    if category == "gpu":
        return table, _SQL_HAS_GPU
    if category == "ram":
        return table, _SQL_IS_RAM
    if category == "storage":
        return table, _SQL_IS_STORAGE
    if category == "psu":
        return table, _SQL_IS_PSU
    raise ValueError(f"unknown category: {category}")


def _upsert_node(cur: Any, node_id: str, node_type: str, label: str) -> None:
    cur.execute(
        """
        INSERT INTO graph_nodes (id, type, label, properties)
        VALUES (%s, %s, %s, '{}'::jsonb)
        ON CONFLICT (id) DO UPDATE SET
            type = EXCLUDED.type,
            label = EXCLUDED.label
        """,
        (node_id, node_type, label),
    )


def _upsert_edge(cur: Any, src: str, relation: str, dst: str, properties: dict[str, Any] | None = None) -> None:
    props = properties or {}
    cur.execute(
        """
        INSERT INTO graph_edges (src, relation, dst, properties, properties_hash)
        VALUES (%s, %s, %s, %s::jsonb, %s)
        ON CONFLICT (src, relation, dst, properties_hash) DO NOTHING
        """,
        (src, relation, dst, json.dumps(props), json.dumps(props, sort_keys=True)),
    )


def _populate_nodes(cur: Any, source: str) -> int:
    cur.execute(_SQL_PRODUCTS, (source,))
    rows = cur.fetchall()
    for r in rows:
        _upsert_node(cur, r["id"], "product", r["name"])
    return len(rows)


def _populate_cpu_mobo_edges(cur: Any, source: str) -> int:
    cur.execute(_SQL_CPUS, (source,))
    cpus = [dict(r) for r in cur.fetchall()]
    cur.execute(_SQL_MOBOS, (source,))
    mobos = [dict(r) for r in cur.fetchall()]
    n = 0
    for cpu in cpus:
        for mobo in mobos:
            if cpu["socket"] and mobo["socket"] and cpu["socket"] == mobo["socket"]:
                _upsert_edge(cur, cpu["id"], "compatible_with", mobo["id"], {"via": "socket"})
                _upsert_edge(cur, mobo["id"], "compatible_with", cpu["id"], {"via": "socket"})
                n += 2
    return n


def _populate_ram_mobo_edges(cur: Any, source: str) -> int:
    cur.execute(_SQL_RAMS, (source,))
    rams = [dict(r) for r in cur.fetchall()]
    cur.execute(_SQL_MOBOS, (source,))
    mobos = [dict(r) for r in cur.fetchall()]
    n = 0
    for ram in rams:
        for mobo in mobos:
            if ram["ram_type"] and mobo["ram_type"] and ram["ram_type"] == mobo["ram_type"]:
                _upsert_edge(cur, ram["id"], "compatible_with", mobo["id"], {"via": "ram_type"})
                _upsert_edge(cur, mobo["id"], "compatible_with", ram["id"], {"via": "ram_type"})
                n += 2
    return n


def _populate_substitutes(cur: Any, source: str, price_delta: float) -> int:
    n = 0
    for cat in ("cpu", "gpu", "ram", "storage", "psu"):
        table, predicate = _category_predicate(cat)
        sql = f"""
        SELECT p.id, COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd
        FROM {table}
        WHERE p.status = 'active' AND p.deleted_at IS NULL AND p.source = %s
          AND {predicate}
          AND COALESCE(cp.price_vnd, p.price_vnd) IS NOT NULL
        """
        cur.execute(sql, (source,))
        items = [dict(r) for r in cur.fetchall()]
        for i, a in enumerate(items):
            for b in items[i + 1 :]:
                pa, pb = float(a["price_vnd"]), float(b["price_vnd"])
                if pa <= 0 or pb <= 0:
                    continue
                if abs(pa - pb) / max(pa, pb) <= price_delta:
                    _upsert_edge(cur, a["id"], "substitutes", b["id"], {"price_delta": price_delta})
                    _upsert_edge(cur, b["id"], "substitutes", a["id"], {"price_delta": price_delta})
                    n += 2
    return n


def build_graph(source: str, price_delta: float = PRICE_DELTA_DEFAULT) -> dict[str, int]:
    logger.info("Building graph for source=%s, price_delta=%.2f", source, price_delta)
    stats = {"nodes": 0, "cpu_mobo_edges": 0, "ram_mobo_edges": 0, "substitute_edges": 0}
    with connect() as conn:
        with conn.cursor() as cur:
            stats["nodes"] = _populate_nodes(cur, source)
            stats["cpu_mobo_edges"] = _populate_cpu_mobo_edges(cur, source)
            stats["ram_mobo_edges"] = _populate_ram_mobo_edges(cur, source)
            stats["substitute_edges"] = _populate_substitutes(cur, source, price_delta)
        conn.commit()
    logger.info("Graph built: %s", stats)
    return stats


def cmd_build(args: argparse.Namespace) -> int:
    source = args.source or get_default_source()
    stats = build_graph(source=source, price_delta=args.price_delta)
    print(json.dumps(stats, indent=2))
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    parser = argparse.ArgumentParser(description="M5b graph population")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="Populate graph_nodes + graph_edges")
    p_build.add_argument("--source", default=None, help="Product source (default anphatpc)")
    p_build.add_argument("--price-delta", type=float, default=PRICE_DELTA_DEFAULT, help="Substitute price delta (default 0.20)")
    p_build.set_defaults(func=cmd_build)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
