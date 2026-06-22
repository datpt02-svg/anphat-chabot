"""M2 hashing utilities.

All hashes are sha256 hex lowercase, computed over a stable canonical JSON
representation with `sort_keys=True` and `separators=(',', ':')`. This keeps
`canonical_hash` stable across key-order changes in upstream `products_final.json`.

Per locked M2 plan §"Decisions locked for M2":
- product_id, payload_hash, price_hash, content_hash all use this scheme.
- id_suffix_8 = sha256(source_url).hexdigest()[:8]
- product_id  = f"{source}:{sha256_hex(source_url)[:16]}"
- slug        = slugify(name, max_length=180) + '-' + id_suffix_8
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any


def sha256_hex(value: str) -> str:
    """sha256 hex lowercase of an arbitrary string."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_json(value: Any) -> str:
    """Stable JSON: sort_keys, no whitespace, UTF-8 safe."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_canonical(value: Any) -> str:
    """sha256_hex of canonical_json(value)."""
    return sha256_hex(canonical_json(value))


def product_id_from_url(source: str, source_url: str) -> str:
    """Locked: f"{source}:{sha256_hex(source_url)[:16]}"."""
    return f"{source}:{sha256_hex(source_url)[:16]}"


def id_suffix_8(source_url: str) -> str:
    """Locked: first 8 hex chars of sha256(source_url) for slug suffix."""
    return sha256_hex(source_url)[:8]


def slugify(name: str, max_length: int = 180) -> str:
    """Convert name to URL-safe slug. Strips diacritics, lowercases, non-alnum -> '-'.

    Vietnamese: Máy tính -> may-tinh, Đặc biệt -> dac-biet.
    The 'đ' letter is not decomposed by NFD; map it manually.
    """
    if not name:
        return ""

    # Vietnamese: 'đ' is its own letter (U+0110), not a base+combining mark.
    text = name.replace("Đ", "D").replace("đ", "d")

    # Normalize unicode (NFD) then strip combining marks (accents)
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")

    text = text.lower()
    # Replace any non-alphanumeric ASCII char with '-'
    text = re.sub(r"[^a-z0-9]+", "-", text)
    # Collapse multiple dashes
    text = re.sub(r"-+", "-", text)
    # Trim leading/trailing dashes
    text = text.strip("-")

    if max_length > 0 and len(text) > max_length:
        text = text[:max_length].rstrip("-")

    return text


def make_slug(name: str, source_url: str, max_name_length: int = 180) -> str:
    """Locked: slugify(name, max_length=180) + '-' + id_suffix_8."""
    return f"{slugify(name, max_name_length)}-{id_suffix_8(source_url)}"


# Unit whitelist for product_spec_values.unit derivation.
# Order matters: longer first to avoid partial matches (e.g. 'mhz' before 'hz').
# Plural forms (inches) are accepted as synonyms for the canonical form (inch).
_UNIT_SYNONYMS = {
    "inches": "inch",
    "mbs": "mb",
    "gbs": "gb",
    "tbs": "tb",
    "mgs": "mg",
    "kgs": "kg",
}

_UNITS_BY_LENGTH = sorted(
    [
        "gbps",
        "mbps",
        "w_h",
        "ghz",
        "mhz",
        "fps",
        "dpi",
        "inch",
        "rpm",
        "gb",
        "mb",
        "tb",
        "mm",
        "cm",
        "kg",
        "mp",
        "hz",
        "w",
        "g",
        "°",
    ],
    key=len,
    reverse=True,
)


def extract_unit(key: str) -> str | None:
    """Return a unit from the whitelist if the key ends with one, else None.

    Examples:
        ram_gb -> 'gb'
        cpu_base_clock_ghz -> 'ghz'
        screen_inches -> 'inch' (synonym 'inches' mapped to 'inch')
        weight_kg -> 'kg'
        ports -> None
    """
    if not key:
        return None

    key_lower = key.lower()

    for unit in _UNITS_BY_LENGTH:
        suffix = "_" + unit
        if key_lower.endswith(suffix) or key_lower == unit or key_lower.endswith(unit):
            return unit

    # Try plural synonym: '_inches' -> 'inch' etc.
    for syn, canon in _UNIT_SYNONYMS.items():
        if key_lower.endswith("_" + syn) or key_lower == syn:
            return canon

    return None


def coerce_number(value: Any) -> float | int | None:
    """Try to parse value as int, then float. Else None.

    Booleans are treated as non-numeric and return None.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace(" ", "")
        if not s:
            return None
        try:
            if s.lstrip("-").isdigit():
                return int(s)
            return float(s)
        except ValueError:
            return None
    return None


# Group whitelist for product_spec_values.group_name derivation.
_GROUP_WHITELIST = {
    "cpu": "cpu",
    "cpu_model": "cpu",
    "cpu_cores": "cpu",
    "cpu_threads": "cpu",
    "cpu_base_clock_ghz": "cpu",
    "cpu_boost_clock_ghz": "cpu",
    "socket": "cpu",
    "ram": "ram",
    "ram_gb": "ram",
    "ram_type": "ram",
    "ram_speed_mhz": "ram",
    "max_ram_gb": "ram",
    "ram_slots": "ram",
    "ram_standard": "ram",
    "storage": "storage",
    "storage_gb": "storage",
    "storage_type": "storage",
    "storage_detail": "storage",
    "upgrade_storage_options": "storage",
    "gpu": "gpu",
    "gpu_model": "gpu",
    "gpu_vram_gb": "gpu",
    "gpu_vram_type": "gpu",
    "screen": "screen",
    "screen_inches": "screen",
    "resolution_label": "screen",
    "resolution_width": "screen",
    "resolution_height": "screen",
    "refresh_rate_hz": "screen",
    "panel_type": "screen",
    "connectivity": "connectivity",
    "warranty": "warranty",
    "warranty_months": "warranty",
    "os": "os",
    "ports": "ports",
}


def derive_group(key: str) -> str:
    """Map spec_key -> group_name via whitelist. Default: 'other'."""
    return _GROUP_WHITELIST.get(key, "other")


def price_hash_components(price_vnd, list_price_vnd, sale_price_vnd,
                          build_pc_price_vnd, regional_price_vnd,
                          stock_status, stock_quantity) -> str:
    """sha256_hex of canonical JSON of the 7-component price state vector."""
    payload = {
        "price_vnd": price_vnd,
        "list_price_vnd": list_price_vnd,
        "sale_price_vnd": sale_price_vnd,
        "build_pc_price_vnd": build_pc_price_vnd,
        "regional_price_vnd": regional_price_vnd,
        "stock_status": stock_status,
        "stock_quantity": stock_quantity,
    }
    return hash_canonical(payload)


def payload_hash(payload: Any) -> str:
    """Hash an arbitrary JSON-serializable payload (raw row)."""
    return hash_canonical(payload)
