"""Build Meilisearch product documents from PostgreSQL rows."""
from __future__ import annotations

import re
import unicodedata
from decimal import Decimal
from typing import Any, Iterable

from scripts.m3_search.db import connect

SQL_ACTIVE_PRODUCTS_PAGE = """
SELECT
  p.id,
  p.source,
  p.source_url,
  p.source_product_id,
  p.sku,
  p.slug,
  p.name,
  p.brand,
  p.category,
  p.subcategory,
  p.thumbnail_url,
  p.images,
  p.price_vnd,
  p.list_price_vnd,
  p.sale_price_vnd,
  p.build_pc_price_vnd,
  p.regional_price_vnd,
  p.stock_status,
  p.stock_quantity,
  p.warranty_text,
  p.warranty_months,
  p.description,
  p.breadcrumbs,
  p.status,
  p.updated_at,
  ps.product_type,
  ps.model,
  ps.cpu_model,
  ps.socket,
  ps.ram_gb,
  ps.ram_type,
  ps.ram_speed_mhz,
  ps.storage_gb,
  ps.storage_type,
  ps.gpu_model,
  ps.gpu_vram_gb,
  ps.screen_inches,
  ps.resolution_label,
  ps.refresh_rate_hz,
  ps.panel_type,
  ps.form_factor,
  ps.psu_wattage_w,
  ps.recommended_psu_w,
  cp.price_vnd AS current_price_vnd,
  cp.list_price_vnd AS current_list_price_vnd,
  cp.sale_price_vnd AS current_sale_price_vnd,
  cp.stock_status AS current_stock_status
FROM products p
LEFT JOIN product_specs ps ON ps.product_id = p.id
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
WHERE p.source = %s
  AND p.status = 'active'
  AND p.deleted_at IS NULL
ORDER BY p.id
LIMIT %s OFFSET %s
"""

SQL_PRODUCTS_BY_IDS = SQL_ACTIVE_PRODUCTS_PAGE.replace(
    "WHERE p.source = %s\n  AND p.status = 'active'\n  AND p.deleted_at IS NULL",
    "WHERE p.id = ANY(%s::text[])\n  AND p.status = 'active'\n  AND p.deleted_at IS NULL",
).replace("LIMIT %s OFFSET %s", "")

SQL_ACTIVE_COUNT = """
SELECT count(*) AS c
FROM products
WHERE source = %s AND status = 'active' AND deleted_at IS NULL
"""


def _as_jsonable(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def sanitize_id(product_id: str) -> str:
    """Meili document IDs must be alphanumeric / `-` / `_` only (no `:`).

    The product PK is `{source}:{hash}`. We replace `:` with `_` so the value
    is a valid Meili document identifier while remaining globally unique and
    human-readable.
    """
    if not product_id:
        return product_id
    return product_id.replace(":", "_")


def _unaccent(value: str) -> str:
    text = value.replace("Đ", "D").replace("đ", "d")
    text = unicodedata.normalize("NFD", text)
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


def normalize_breadcrumbs(value: Any) -> list[str]:
    if not value:
        return []
    out: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, str) and item.strip():
                out.append(item.strip())
            elif isinstance(item, dict):
                label = item.get("name") or item.get("label") or item.get("title")
                if isinstance(label, str) and label.strip():
                    out.append(label.strip())
    return out


def _add_token(tokens: list[str], seen: set[str], value: Any) -> None:
    if value is None:
        return
    text = str(value).strip().lower()
    if not text:
        return
    if text not in seen:
        seen.add(text)
        tokens.append(text)


def build_normalized_tokens(row: dict) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()

    cpu = row.get("cpu_model")
    if cpu:
        cpu_l = str(cpu).lower()
        m = re.search(r"\bi([3579])-?\s*([0-9]{4,5}[a-z]*)\b", cpu_l)
        if m:
            model = f"i{m.group(1)}-{m.group(2)}"
            _add_token(tokens, seen, model)
            _add_token(tokens, seen, model.replace("-", " "))
            _add_token(tokens, seen, model.replace("-", ""))
        m = re.search(r"ryzen\s+([3579])\s+([0-9]{4,5}[a-z]*)", cpu_l)
        if m:
            _add_token(tokens, seen, f"ryzen {m.group(1)} {m.group(2)}")
            _add_token(tokens, seen, f"r{m.group(1)} {m.group(2)}")

    gpu = row.get("gpu_model")
    if gpu:
        gpu_l = str(gpu).lower()
        m = re.search(r"\b(rtx|gtx|rx)\s*([0-9]{3,4})(\s*ti)?\b", gpu_l)
        if m:
            prefix, number, ti = m.group(1), m.group(2), (m.group(3) or "").strip()
            spaced = f"{prefix} {number}" + (f" {ti}" if ti else "")
            compact = spaced.replace(" ", "")
            _add_token(tokens, seen, spaced)
            _add_token(tokens, seen, compact)
            if ti:
                _add_token(tokens, seen, f"{number} {ti}")

    ram_gb = row.get("ram_gb")
    ram_type = row.get("ram_type")
    if ram_gb:
        _add_token(tokens, seen, f"{ram_gb}gb")
        _add_token(tokens, seen, f"{ram_gb} gb")
        if ram_type:
            rt = str(ram_type).lower()
            _add_token(tokens, seen, rt)
            _add_token(tokens, seen, f"{ram_gb}gb {rt}")

    storage_gb = row.get("storage_gb")
    storage_type = (str(row.get("storage_type") or "")).lower()
    if storage_gb:
        _add_token(tokens, seen, f"{storage_gb}gb")
        _add_token(tokens, seen, f"{storage_gb} gb")
        if "ssd" in storage_type:
            _add_token(tokens, seen, f"ssd{storage_gb}")
        try:
            n = int(storage_gb)
            if n >= 1024 and n % 1024 == 0:
                _add_token(tokens, seen, f"{n // 1024}tb")
        except (TypeError, ValueError):
            pass

    screen_inches = row.get("screen_inches")
    if screen_inches:
        text = str(screen_inches).rstrip("0").rstrip(".")
        _add_token(tokens, seen, f"{text} inch")
        _add_token(tokens, seen, f"{text}inch")

    refresh_rate = row.get("refresh_rate_hz")
    if refresh_rate:
        _add_token(tokens, seen, f"{refresh_rate}hz")
        _add_token(tokens, seen, f"{refresh_rate} hz")

    for key in ("name", "category", "subcategory", "brand"):
        value = row.get(key)
        if isinstance(value, str):
            ascii_text = _unaccent(value).lower()
            if ascii_text != value.lower():
                _add_token(tokens, seen, ascii_text)

    return tokens


def build_spec_summary(row: dict) -> str:
    parts: list[str] = []
    if row.get("cpu_model"):
        parts.append(str(row["cpu_model"]))
    if row.get("ram_gb"):
        ram = f"{row['ram_gb']}GB"
        if row.get("ram_type"):
            ram += f" {row['ram_type']}"
        parts.append(ram)
    if row.get("storage_gb"):
        storage = f"{row.get('storage_type') or 'Storage'} {row['storage_gb']}GB"
        parts.append(storage)
    if row.get("gpu_model"):
        parts.append(str(row["gpu_model"]))
    if row.get("screen_inches"):
        screen = f"{row['screen_inches']} inch"
        if row.get("refresh_rate_hz"):
            screen += f" {row['refresh_rate_hz']}Hz"
        parts.append(screen)
    return " / ".join(parts)


def build_document(row: dict) -> dict:
    price_vnd = row.get("current_price_vnd") if row.get("current_price_vnd") is not None else row.get("price_vnd")
    stock_status = row.get("current_stock_status") or row.get("stock_status")
    raw_id = row.get("id")
    doc = {
        "id": sanitize_id(raw_id) if raw_id else None,
        "product_id": raw_id,
        "slug": row.get("slug"),
        "source": row.get("source"),
        "source_url": row.get("source_url"),
        "sku": row.get("sku"),
        "name": row.get("name"),
        "brand": row.get("brand"),
        "category": row.get("category"),
        "subcategory": row.get("subcategory"),
        "breadcrumbs": normalize_breadcrumbs(row.get("breadcrumbs")),
        "thumbnail_url": row.get("thumbnail_url"),
        "price_vnd": price_vnd,
        "list_price_vnd": row.get("current_list_price_vnd") if row.get("current_list_price_vnd") is not None else row.get("list_price_vnd"),
        "sale_price_vnd": row.get("current_sale_price_vnd") if row.get("current_sale_price_vnd") is not None else row.get("sale_price_vnd"),
        "build_pc_price_vnd": row.get("build_pc_price_vnd"),
        "regional_price_vnd": row.get("regional_price_vnd"),
        "stock_status": stock_status,
        "stock_quantity": row.get("stock_quantity"),
        "warranty_months": row.get("warranty_months"),
        "warranty_text": row.get("warranty_text"),
        "product_type": row.get("product_type"),
        "model": row.get("model"),
        "cpu_model": row.get("cpu_model"),
        "socket": row.get("socket"),
        "ram_gb": row.get("ram_gb"),
        "ram_type": row.get("ram_type"),
        "ram_speed_mhz": row.get("ram_speed_mhz"),
        "storage_gb": row.get("storage_gb"),
        "storage_type": row.get("storage_type"),
        "gpu_model": row.get("gpu_model"),
        "gpu_vram_gb": row.get("gpu_vram_gb"),
        "screen_inches": _as_jsonable(row.get("screen_inches")),
        "resolution_label": row.get("resolution_label"),
        "refresh_rate_hz": row.get("refresh_rate_hz"),
        "panel_type": row.get("panel_type"),
        "form_factor": row.get("form_factor"),
        "psu_wattage_w": row.get("psu_wattage_w"),
        "recommended_psu_w": row.get("recommended_psu_w"),
        "updated_at": _as_jsonable(row.get("updated_at")),
    }
    doc["spec_summary"] = build_spec_summary(doc)
    doc["normalized_tokens"] = build_normalized_tokens(doc)
    return doc


def iter_product_documents(source: str, batch_size: int) -> Iterable[list[dict]]:
    offset = 0
    with connect() as conn:
        with conn.cursor() as cur:
            while True:
                cur.execute(SQL_ACTIVE_PRODUCTS_PAGE, (source, batch_size, offset))
                rows = cur.fetchall()
                if not rows:
                    break
                yield [build_document(dict(r)) for r in rows]
                offset += batch_size


def load_documents_for_ids(product_ids: list[str]) -> list[dict]:
    if not product_ids:
        return []
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_PRODUCTS_BY_IDS, (product_ids,))
            return [build_document(dict(r)) for r in cur.fetchall()]


def count_active_products(source: str) -> int:
    with connect() as conn:
        with conn.cursor() as cur:
            cur.execute(SQL_ACTIVE_COUNT, (source,))
            return int(cur.fetchone()["c"])
