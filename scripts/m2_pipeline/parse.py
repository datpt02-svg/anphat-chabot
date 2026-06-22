"""M2 parser: load `products_final.json` and validate rows.

Pure functions only (except `load_products_json` which is file IO).
The orchestrator (`pipeline.py`) owns counters, logging and DB writes.

Per locked M2 plan §Stage 1:
- Validate required: source_url, name, category. Skip + warning if fail.
- Parse prices.* (digits only -> int | None)
- Parse stock.status -> standard enum (fallback 'unknown')
- Parse crawled_at: ISO + handle `+0700` -> `+07:00`; fallback `now()`
- Parse warranty_months: prefer normalized_specs (0 valid), fallback regex
  r"(\\d+)\\s*tháng" on warranty text. Both None/missing -> NULL.
- Normalize list fields: images=[], breadcrumbs=[] if None.
"""
from __future__ import annotations

import json
import re
import warnings
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

from scripts.m2_pipeline.config import MAX_FILE_SIZE_WARN_BYTES, STOCK_STATUSES
from scripts.m2_pipeline.hashing import product_id_from_url


# --- helpers --------------------------------------------------------------


_OFFSET_RE = re.compile(r"([+-])(\d{2})(\d{2})$")
_WARRANTY_RE = re.compile(r"(\d+)\s*tháng", re.IGNORECASE)


def parse_timestamp(value: Any) -> Optional[datetime]:
    """Parse ISO 8601 string. Handle `+0700` -> `+07:00`. Return aware UTC or None.

    Falls back to None on failure; the caller decides what to do (often `now()`).
    """
    if not value or not isinstance(value, str):
        return None

    text = value.strip()
    if not text:
        return None

    # Normalize offset `+0700` -> `+07:00` (Python 3.11 accepts this in most
    # cases, but normalize explicitly for safety).
    match = _OFFSET_RE.search(text)
    if match:
        sign, hh, mm = match.groups()
        text = text[: match.start()] + f"{sign}{hh}:{mm}"

    try:
        dt = datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return None

    # If naive, assume UTC
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt


def parse_warranty_months(
    warranty_text: Optional[str],
    normalized_warranty: Any,
) -> Optional[int]:
    """Resolve warranty_months per locked rules:

    1. If normalized_specs.warranty_months is a number (including 0), use it.
    2. Else, regex "(\\d+)\\s*tháng" on warranty text. 0 is valid.
    3. Else None.
    """
    if isinstance(normalized_warranty, bool):
        return None
    if isinstance(normalized_warranty, (int, float)):
        return int(normalized_warranty)

    if warranty_text:
        match = _WARRANTY_RE.search(warranty_text)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return None

    return None


def parse_price_vnd(value: Any) -> Optional[int]:
    """Coerce a price field to int VND or None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        s = value.strip().replace(",", "").replace(".", "").replace(" ", "")
        if not s:
            return None
        try:
            n = int(s)
            return n if n >= 0 else None
        except ValueError:
            return None
    return None


def parse_stock_status(value: Any) -> Optional[str]:
    """Coerce to standard enum; return None if input is missing/empty.

    Unknown values are returned as-is so the DB CHECK constraint can reject
    (we log a warning at parse time). The pipeline treats None as 'unknown'.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    s = value.strip().lower()
    if not s:
        return None
    if s in STOCK_STATUSES:
        return s
    return s  # leave to DB CHECK


def parse_stock_quantity(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        return int(value) if value >= 0 else None
    if isinstance(value, str):
        s = value.strip()
        if not s:
            return None
        try:
            n = int(s)
            return n if n >= 0 else None
        except ValueError:
            return None
    return None


# --- ParsedRow ------------------------------------------------------------


@dataclass
class ParsedRow:
    """Cleaned/normalized view of one `products_final.json` row."""

    raw: dict

    # Identifiers
    source: str
    source_url: str
    product_id: str
    name: str
    category: str

    # Optional identifiers
    subcategory: Optional[str]
    brand: Optional[str]
    sku: Optional[str]
    source_product_id: Optional[str]
    thumbnail_url: Optional[str]

    # Content
    images: list[str] = field(default_factory=list)
    description: Optional[str] = None
    breadcrumbs: list[str] = field(default_factory=list)
    raw_specs: dict = field(default_factory=dict)
    normalized_specs: dict = field(default_factory=dict)
    validation_warnings: list = field(default_factory=list)
    llm_warnings: list = field(default_factory=list)
    raw_html_path: Optional[str] = None

    # Timestamps
    crawled_at: Optional[datetime] = None
    normalized_at: Optional[datetime] = None

    # Prices
    list_price_vnd: Optional[int] = None
    sale_price_vnd: Optional[int] = None
    build_pc_price_vnd: Optional[int] = None
    regional_price_vnd: Optional[int] = None

    # Stock
    stock_status: Optional[str] = None
    stock_quantity: Optional[int] = None

    # Warranty
    warranty_text: Optional[str] = None
    warranty_months: Optional[int] = None

    # Index in the source file (1-based, for crawl_errors.raw->'row_index')
    row_index: int = 0

    # Whether crawled_at came from the source (False = fell back to now())
    crawled_at_from_source: bool = True


# --- parser ---------------------------------------------------------------


REQUIRED_FIELDS = ("source_url", "name", "category")


def parse_row(raw: dict, row_index: int, default_source: str) -> Optional[ParsedRow]:
    """Parse one raw row. Return None if any required field is missing.

    Does NOT raise on missing fields - per plan §7.0 (parse-time fail is a
    counter increment, not an exception).
    """
    for field_name in REQUIRED_FIELDS:
        if not raw.get(field_name):
            warnings.warn(
                f"row {row_index}: missing required field {field_name!r}",
                stacklevel=2,
            )
            return None

    source = (raw.get("source") or default_source).strip() or default_source

    prices = raw.get("prices") or {}
    stock = raw.get("stock") or {}
    warranty_text = raw.get("warranty")
    normalized_specs = raw.get("normalized_specs") or {}

    warranty_months = parse_warranty_months(
        warranty_text,
        normalized_specs.get("warranty_months"),
    )

    crawled_at = parse_timestamp(raw.get("crawled_at"))
    normalized_at = parse_timestamp(raw.get("normalized_at"))

    return ParsedRow(
        raw=raw,
        source=source,
        source_url=str(raw["source_url"]).strip(),
        product_id=product_id_from_url(source, str(raw["source_url"]).strip()),
        name=str(raw["name"]).strip(),
        category=str(raw["category"]).strip(),
        subcategory=raw.get("subcategory"),
        brand=raw.get("brand"),
        sku=raw.get("sku"),
        source_product_id=raw.get("source_product_id"),
        thumbnail_url=raw.get("thumbnail_url"),
        images=list(raw.get("images") or []),
        description=raw.get("description"),
        breadcrumbs=list(raw.get("breadcrumbs") or []),
        raw_specs=dict(raw.get("raw_specs") or {}),
        normalized_specs=dict(normalized_specs),
        validation_warnings=list(raw.get("validation_warnings") or []),
        llm_warnings=list(raw.get("llm_warnings") or []),
        raw_html_path=raw.get("raw_html_path"),
        crawled_at=crawled_at,
        normalized_at=normalized_at,
        list_price_vnd=parse_price_vnd(prices.get("list_price")),
        sale_price_vnd=parse_price_vnd(prices.get("sale_price")),
        build_pc_price_vnd=parse_price_vnd(prices.get("build_pc_price")),
        regional_price_vnd=parse_price_vnd(prices.get("regional_price")),
        stock_status=parse_stock_status(stock.get("status")),
        stock_quantity=parse_stock_quantity(stock.get("quantity")),
        warranty_text=warranty_text,
        warranty_months=warranty_months,
        row_index=row_index,
        crawled_at_from_source=crawled_at is not None,
    )


# --- file IO --------------------------------------------------------------


def load_products_json(path: Path) -> list[dict]:
    """Load `products_final.json` (or jsonl). Raise on malformed JSON."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {path}")

    if path.suffix == ".jsonl":
        out: list[dict] = []
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                out.append(json.loads(line))
        return out

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError(f"Input JSON must be a list of products: {path}")
    return data


def check_file_size(path: Path) -> int:
    """Return file size in bytes; warn if above threshold."""
    size = Path(path).stat().st_size
    if size > MAX_FILE_SIZE_WARN_BYTES:
        warnings.warn(
            f"Input file is {size / (1024*1024):.0f} MB > "
            f"{MAX_FILE_SIZE_WARN_BYTES // (1024*1024)} MB threshold; "
            f"consider ijson streaming.",
            stacklevel=2,
        )
    return size


def iter_parsed_rows(
    raw_rows: list[dict],
    default_source: str,
) -> Iterator[ParsedRow]:
    """Yield ParsedRow for each raw row, skipping invalid ones (caller counts)."""
    for idx, row in enumerate(raw_rows, start=1):
        parsed = parse_row(row, row_index=idx, default_source=default_source)
        if parsed is not None:
            yield parsed
