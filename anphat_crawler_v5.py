from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict, deque
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup, Tag

ROOT_SITEMAP_URL = "https://www.anphatpc.com.vn/sitemap.xml"
PC_SITEMAP_PATTERN = re.compile(
    r"/sitemap_pc\d+\.xml$",
    flags=re.IGNORECASE,
)

CATEGORY_PAGE_PATTERN = re.compile(
    r"_dm\d+\.html(?:-\d+)?$",
    flags=re.IGNORECASE,
)

PAGINATION_PATTERN = re.compile(
    r"\.html-\d+$",
    flags=re.IGNORECASE,
)


def parse_sitemap_locations(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)

    locations: list[str] = []

    for element in root.iter():
        tag_name = element.tag.split("}")[-1]

        if tag_name != "loc" or not element.text:
            continue

        locations.append(
            element.text.strip()
        )

    return locations


def is_xml_sitemap_url(url: str) -> bool:
    path = urlparse(url).path.lower()

    return path.endswith(".xml")


def is_pc_sitemap_url(url: str) -> bool:
    path = urlparse(url).path.lower()

    return bool(
        PC_SITEMAP_PATTERN.search(path)
    )


def looks_like_product_detail_url(url: str) -> bool:
    """
    Chỉ giữ URL có khả năng là product detail.

    Loại:
    - category landing page: *_dm1234.html
    - pagination/listing page: *.html-1
    - URL không kết thúc bằng .html
    """
    path = urlparse(url).path.lower()

    if not path.endswith(".html"):
        return False

    if CATEGORY_PAGE_PATTERN.search(path):
        return False

    if PAGINATION_PATTERN.search(path):
        return False

    return True


def discover_product_urls(
    client,
    *,
    limit: int,
    max_pc_sitemaps: int | None = None,
) -> list[str]:
    """
    Đi từ sitemap.xml:
    - recurse qua sitemap con;
    - chỉ lấy product URL từ sitemap_pc<ID>.xml;
    - bỏ sitemap category và URL listing;
    - dừng khi đủ candidate.
    """
    queue: deque[str] = deque(
        [ROOT_SITEMAP_URL]
    )

    visited_sitemaps: set[str] = set()
    seen_products: set[str] = set()
    product_urls: list[str] = []

    scanned_pc_sitemaps = 0

    while queue and len(product_urls) < limit:
        sitemap_url = queue.popleft()

        if sitemap_url in visited_sitemaps:
            continue

        visited_sitemaps.add(sitemap_url)

        if is_pc_sitemap_url(sitemap_url):
            scanned_pc_sitemaps += 1

            if (
                max_pc_sitemaps is not None
                and scanned_pc_sitemaps > max_pc_sitemaps
            ):
                break

        print(
            f"GET sitemap [{scanned_pc_sitemaps}] "
            f"{sitemap_url}",
            flush=True,
        )

        xml_text = client.get_sitemap_text(
            sitemap_url
        )

        locations = parse_sitemap_locations(
            xml_text
        )

        child_sitemaps = [
            url
            for url in locations
            if is_xml_sitemap_url(url)
        ]

        if child_sitemaps:
            for child_url in child_sitemaps:
                if child_url not in visited_sitemaps:
                    queue.append(child_url)

            continue

        if not is_pc_sitemap_url(sitemap_url):
            continue

        added_count = 0

        for url in locations:
            if not looks_like_product_detail_url(url):
                continue

            if url in seen_products:
                continue

            seen_products.add(url)
            product_urls.append(url)
            added_count += 1

            if len(product_urls) >= limit:
                break

        print(
            f"  -> +{added_count} product URL(s), "
            f"total={len(product_urls)}/{limit}",
            flush=True,
        )

    print(
        f"Discovery complete: "
        f"{len(product_urls)} product URL(s), "
        f"scanned {scanned_pc_sitemaps} PC sitemap(s)",
        flush=True,
    )

    return product_urls


BASE_URL = "https://www.anphatpc.com.vn"
ALLOWED_HOSTS = {"anphatpc.com.vn", "www.anphatpc.com.vn"}
DEFAULT_SITEMAP_CANDIDATES = [
    "https://anphatpc.com.vn/sitemap.xml",
    "https://www.anphatpc.com.vn/sitemap.xml",
    "https://anphatpc.com.vn/sitemap_pc2993.xml",
    "https://www.anphatpc.com.vn/sitemap_pc2993.xml",
]
DEFAULT_ENTRY_PAGES = [
    "https://www.anphatpc.com.vn/",
    "https://www.anphatpc.com.vn/buildpc",
]
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/149.0.0.0 Safari/537.36 "
    "AnPhatCatalogResearchBot/1.0"
)
TRACKING_QUERY_KEYS = {
    "srsltid",
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "gclid",
    "fbclid",
}

# Output categories for sale bot and PC builder.
CATEGORY_ORDER = [
    "cpu",
    "mainboard",
    "ram",
    "gpu",
    "ssd",
    "hdd",
    "psu",
    "case",
    "cooler",
    "case_fan",
    "monitor",
    "keyboard",
    "mouse",
    "headset",
    "webcam",
    "laptop",
    "desktop_pc",
    "ups",
    "other",
]

CATEGORY_HINTS: dict[str, tuple[str, ...]] = {
    "cpu": (
        "cpu intel",
        "cpu amd",
        "bo vi xu ly",
        "processor",
        "core i3",
        "core i5",
        "core i7",
        "core i9",
        "core ultra",
        "ryzen 3",
        "ryzen 5",
        "ryzen 7",
        "ryzen 9",
    ),
    "mainboard": (
        "mainboard",
        "bo mach chu",
        "motherboard",
    ),
    "ram": (
        "ram desktop",
        "ram pc",
        "bo nho ram",
        "memory desktop",
    ),
    "gpu": (
        "vga",
        "card man hinh",
        "graphics card",
        "geforce rtx",
        "radeon rx",
    ),
    "ssd": (
        "ssd",
        "o cung the ran",
        "solid state drive",
        "nvme",
    ),
    "hdd": (
        "hdd",
        "o cung hdd",
        "hard drive",
    ),
    "psu": (
        "nguon may tinh",
        "power supply",
        " psu ",
    ),
    "case": (
        "vo case",
        "case may tinh",
        "computer case",
    ),
    "cooler": (
        "tan nhiet cpu",
        "cooler cpu",
        "aio",
        "tan nhiet nuoc",
        "tan nhiet khi",
    ),
    "case_fan": (
        "fan case",
        "quat case",
        "quat tan nhiet",
    ),
    "monitor": (
        "man hinh may tinh",
        "monitor",
    ),
    "keyboard": (
        "ban phim",
        "keyboard",
    ),
    "mouse": (
        "chuot may tinh",
        "gaming mouse",
        "mouse",
    ),
    "headset": (
        "tai nghe",
        "headset",
        "headphone",
    ),
    "webcam": (
        "webcam",
    ),
    "laptop": (
        "laptop",
        "notebook",
        "macbook",
    ),
    "desktop_pc": (
        "pc gaming",
        "pc van phong",
        "pc do hoa",
        "may tinh de ban",
        "desktop pc",
        "workstation",
    ),
    "ups": (
        "bo luu dien",
        "ups",
    ),
}

CATEGORY_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "cpu": ("socket", "cores"),
    "mainboard": ("socket", "ram_type"),
    "ram": ("ram_type", "capacity_gb"),
    "gpu": ("gpu_model", "vram_gb"),
    "ssd": ("capacity_gb",),
    "psu": ("wattage_w",),
    "case": ("supported_mainboard_form_factors",),
    "cooler": ("supported_sockets",),
    "monitor": ("screen_inches", "resolution_label"),
    "keyboard": (),
    "mouse": (),
    "headset": (),
    "laptop": ("cpu_model", "ram_gb", "storage_gb"),
    "desktop_pc": (),
}

EXCLUDED_PATH_PATTERNS = [
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"/media/",
        r"/tin-tuc/",
        r"/news/",
        r"/blog/",
        r"/khuyen-mai",
        r"/lien-he",
        r"/gioi-thieu",
        r"/tra-gop",
        r"/bao-hanh",
        r"/buildpc/?$",
        r"_idtin\d+\.html$",
        r"/tag/",
        r"/brand/",
        r"/search",
        r"/cart",
        r"/login",
        r"/register",
    )
]

PRICE_RE = re.compile(r"(?<!\d)(\d{1,3}(?:[.\s,]\d{3})+|\d{4,})\s*(?:₫|đ|vnd)", re.IGNORECASE)
NUMBER_RE = re.compile(r"-?\d+(?:[.,]\d+)?")


@dataclass
class DiscoveryItem:
    source_url: str
    discovered_from: str
    hint_category: str = "other"
    discovered_at: str = ""


@dataclass
class ProductRecord:
    source: str
    source_url: str
    source_product_id: str | None
    sku: str | None
    category: str
    subcategory: str | None
    name: str
    brand: str | None
    thumbnail_url: str | None
    images: list[str]
    prices: dict[str, int | None]
    stock: dict[str, Any]
    warranty: str | None
    description: str | None
    breadcrumbs: list[str]
    raw_specs: dict[str, str]
    normalized_specs: dict[str, Any]
    parse_warnings: list[str] = field(default_factory=list)
    spec_status: str = "unknown"
    raw_html_path: str | None = None
    crawled_at: str = ""


class RateLimitedHttpClient:
    def __init__(
        self,
        *,
        delay_seconds: float,
        timeout_seconds: float,
        retries: int,
        user_agent: str,
    ) -> None:
        self.delay_seconds = max(delay_seconds, 0.0)
        self.timeout_seconds = timeout_seconds
        self.retries = max(retries, 0)
        self._last_request_at = 0.0
        self.client = httpx.Client(
            follow_redirects=True,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "RateLimitedHttpClient":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def _wait(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        wait_time = self.delay_seconds - elapsed
        if wait_time > 0:
            time.sleep(wait_time)

    def get_text(self, url: str) -> str:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._wait()
            print(f"GET {url}", flush=True)
            try:
                response = self.client.get(url)
                self._last_request_at = time.monotonic()
                print(f"  -> HTTP {response.status_code}, {len(response.text):,} chars", flush=True)
                if response.status_code in {429, 500, 502, 503, 504}:
                    raise httpx.HTTPStatusError(
                        f"retryable status {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                response.raise_for_status()
                return response.text
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                if attempt >= self.retries:
                    break
                backoff = min(30.0, 2 ** attempt + 0.5)
                print(f"  -> retry in {backoff:.1f}s because: {exc}", flush=True)
                time.sleep(backoff)
        assert last_error is not None
        raise last_error


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def fold_text(value: Any) -> str:
    text = clean_text(value).lower()
    normalized = unicodedata.normalize("NFD", text)
    without_marks = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
    return without_marks.replace("đ", "d")


def canonicalize_url(url: str, base_url: str | None = None) -> str:
    absolute = urljoin(base_url or BASE_URL, url)
    parsed = urlparse(absolute)
    host = parsed.netloc.lower()
    if host == "anphatpc.com.vn":
        host = "www.anphatpc.com.vn"
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in TRACKING_QUERY_KEYS
    ]
    path = re.sub(r"/{2,}", "/", parsed.path or "/")
    return urlunparse(("https", host, path, "", urlencode(query), ""))


def is_allowed_host(url: str) -> bool:
    return urlparse(url).netloc.lower() in ALLOWED_HOSTS


def looks_like_excluded_path(url: str) -> bool:
    path = urlparse(url).path
    return any(pattern.search(path) for pattern in EXCLUDED_PATH_PATTERNS)


def infer_category(*values: Any) -> str:
    haystack = " | ".join(fold_text(value) for value in values if value)
    # Specific component categories before laptop/desktop to avoid false positives.
    for category in CATEGORY_ORDER:
        if category == "other":
            continue
        for hint in CATEGORY_HINTS.get(category, ()):
            if fold_text(hint) in haystack:
                return category
    return "other"


def read_jsonl(path: Path) -> Iterator[dict[str, Any]]:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def url_cache_path(cache_dir: Path, url: str) -> Path:
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()  # noqa: S324
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", urlparse(url).path.rstrip("/").split("/")[-1])[:90]
    return cache_dir / f"{slug or 'page'}__{digest[:12]}.html"


def parse_xml_locations(xml_text: str) -> tuple[list[str], list[str]]:
    root = ET.fromstring(xml_text)
    child_sitemaps: list[str] = []
    urls: list[str] = []
    for element in root.iter():
        if element.tag.split("}")[-1].lower() != "loc" or not element.text:
            continue
        location = clean_text(element.text)
        if "sitemap" in location.lower() or location.lower().endswith(".xml"):
            child_sitemaps.append(location)
        else:
            urls.append(location)
    return child_sitemaps, urls


def collect_robots_sitemaps(client: RateLimitedHttpClient) -> list[str]:
    candidates: list[str] = []
    for robots_url in (
        "https://www.anphatpc.com.vn/robots.txt",
        "https://anphatpc.com.vn/robots.txt",
    ):
        try:
            text = client.get_text(robots_url)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN robots unavailable: {robots_url}: {exc}", flush=True)
            continue
        for line in text.splitlines():
            if line.lower().startswith("sitemap:"):
                candidates.append(canonicalize_url(line.split(":", 1)[1].strip()))
    return deduplicate(candidates)


def deduplicate(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def collect_urls_from_sitemap(
    client: RateLimitedHttpClient,
    sitemap_url: str,
    *,
    visited: set[str] | None = None,
    max_urls: int | None = None,
) -> list[str]:
    visited = visited or set()
    canonical = canonicalize_url(sitemap_url)
    if canonical in visited:
        return []
    visited.add(canonical)
    try:
        xml_text = client.get_text(canonical)
        child_sitemaps, urls = parse_xml_locations(xml_text)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN sitemap skipped: {canonical}: {exc}", flush=True)
        return []

    if child_sitemaps:
        result: list[str] = []
        for child in child_sitemaps:
            if max_urls is not None and len(result) >= max_urls:
                break
            remaining = None if max_urls is None else max_urls - len(result)
            result.extend(
                collect_urls_from_sitemap(
                    client,
                    child,
                    visited=visited,
                    max_urls=remaining,
                )
            )
        return deduplicate(result)

    cleaned = [canonicalize_url(url) for url in urls if is_allowed_host(canonicalize_url(url))]
    return deduplicate(cleaned[:max_urls] if max_urls is not None else cleaned)


def anchor_text(anchor: Tag) -> str:
    return clean_text(anchor.get_text(" ", strip=True))


def discover_links_from_page(client: RateLimitedHttpClient, page_url: str) -> list[tuple[str, str]]:
    html = client.get_text(page_url)
    soup = BeautifulSoup(html, "html.parser")
    output: list[tuple[str, str]] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href")
        if not isinstance(href, str):
            continue
        url = canonicalize_url(href, page_url)
        if not is_allowed_host(url):
            continue
        output.append((url, anchor_text(anchor)))
    return output


def looks_like_product_candidate(url: str, label: str = "") -> bool:
    if not is_allowed_host(url) or looks_like_excluded_path(url):
        return False
    path = urlparse(url).path.lower()
    if not path or path == "/":
        return False
    if path.endswith(".xml"):
        return False
    # Product pages on An Phát commonly use .html. Keep non-html paths only when text strongly hints hardware.
    strong_hint = infer_category(path, label) != "other"
    if path.endswith(".html"):
        return True
    return strong_hint and path.count("/") >= 1


def looks_like_category_page(url: str, label: str = "") -> bool:
    if not is_allowed_host(url) or looks_like_excluded_path(url):
        return False
    path = urlparse(url).path.lower()
    if path in {"", "/"}:
        return False
    folded = fold_text(f"{path} {label}")
    category_words = (
        "cpu", "mainboard", "vga", "ram", "ssd", "hdd", "nguon", "case", "tan nhiet",
        "man hinh", "ban phim", "chuot", "tai nghe", "webcam", "laptop", "pc gaming",
    )
    return any(word in folded for word in category_words)


def discover_catalog(
    *,
    client: RateLimitedHttpClient,
    output_jsonl: Path,
    sitemaps: list[str],
    entry_pages: list[str],
    seed_file: Path | None,
    max_sitemap_urls: int | None,
    max_pages_per_seed: int,
) -> None:
    """
    Discover product-detail URLs from the sitemap graph.

    Important:
    - Start from sitemap.xml.
    - Traverse XML sitemap indexes recursively.
    - Never emit URLs directly from sitemap_category.xml.
    - Only emit HTML URLs whose parent sitemap matches sitemap_pc<ID>.xml.
    - Do not crawl category listing pages during the default discovery pass.
    """
    del entry_pages, max_pages_per_seed  # Listing crawl is intentionally disabled.

    existing = {
        canonicalize_url(str(item.get("source_url", "")))
        for item in read_jsonl(output_jsonl)
        if item.get("source_url")
    }

    target_limit = max_sitemap_urls
    root_candidates = deduplicate(
        [ROOT_SITEMAP_URL]
        + [canonicalize_url(item) for item in sitemaps if item]
    )

    queue: deque[str] = deque(root_candidates)
    visited_sitemaps: set[str] = set()
    found: dict[str, DiscoveryItem] = {}

    scanned_sitemaps = 0
    scanned_pc_sitemaps = 0

    print(
        "Discovering PRODUCT URLs from sitemap graph: "
        f"{ROOT_SITEMAP_URL}",
        flush=True,
    )

    while queue:
        if target_limit is not None and len(found) >= target_limit:
            break

        sitemap_url = canonicalize_url(queue.popleft())

        if sitemap_url in visited_sitemaps:
            continue

        visited_sitemaps.add(sitemap_url)
        scanned_sitemaps += 1

        if is_pc_sitemap_url(sitemap_url):
            scanned_pc_sitemaps += 1

        remaining = (
            None
            if target_limit is None
            else target_limit - len(found)
        )

        print(
            f"GET sitemap [{scanned_sitemaps}; "
            f"pc={scanned_pc_sitemaps}; "
            f"remaining={remaining}] {sitemap_url}",
            flush=True,
        )

        try:
            xml_text = client.get_text(sitemap_url)
            child_sitemaps, page_urls = parse_xml_locations(xml_text)
        except Exception as exc:  # noqa: BLE001
            print(
                f"WARN sitemap skipped: {sitemap_url}: {exc}",
                flush=True,
            )
            continue

        for child_url in child_sitemaps:
            child_url = canonicalize_url(child_url, sitemap_url)

            if (
                is_allowed_host(child_url)
                and child_url not in visited_sitemaps
            ):
                queue.append(child_url)

        # sitemap_category.xml contains landing pages. Ignore its HTML URLs.
        if not is_pc_sitemap_url(sitemap_url):
            continue

        added = 0

        for page_url in page_urls:
            url = canonicalize_url(page_url, sitemap_url)

            if not is_allowed_host(url):
                continue

            if not looks_like_product_detail_url(url):
                continue

            if url in found:
                continue

            found[url] = DiscoveryItem(
                source_url=url,
                discovered_from=sitemap_url,
                hint_category=infer_category(url),
                discovered_at=now_iso(),
            )

            added += 1

            if target_limit is not None and len(found) >= target_limit:
                break

        print(
            f"  -> +{added} product detail URL(s), "
            f"total={len(found)}",
            flush=True,
        )

    # Optional manual seeds: accept direct product-detail URLs only.
    if seed_file and seed_file.exists():
        payload = json.loads(
            seed_file.read_text(encoding="utf-8")
        )

        if isinstance(payload, list):
            seed_values = payload
        elif isinstance(payload, dict):
            seed_values = payload.get("seed_product_urls", [])
        else:
            seed_values = []

        for raw_url in seed_values:
            url = canonicalize_url(str(raw_url))

            if not looks_like_product_detail_url(url):
                continue

            found.setdefault(
                url,
                DiscoveryItem(
                    source_url=url,
                    discovered_from=str(seed_file),
                    hint_category=infer_category(url),
                    discovered_at=now_iso(),
                ),
            )

    written = 0

    for url, item in found.items():
        if url in existing:
            continue

        append_jsonl(
            output_jsonl,
            asdict(item),
        )

        written += 1

    print(
        "Discovery complete: "
        f"{len(found)} product candidate(s), "
        f"appended {written} new URL(s), "
        f"scanned {scanned_sitemaps} sitemap(s), "
        f"including {scanned_pc_sitemaps} PC sitemap(s)",
        flush=True,
    )

def parse_json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []
    for script in soup.select('script[type="application/ld+json"]'):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            objects.append(value)
        elif isinstance(value, list):
            objects.extend(item for item in value if isinstance(item, dict))
    return objects


def flatten_json_ld(objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    queue = list(objects)
    flattened: list[dict[str, Any]] = []
    while queue:
        item = queue.pop(0)
        flattened.append(item)
        graph = item.get("@graph")
        if isinstance(graph, list):
            queue.extend(child for child in graph if isinstance(child, dict))
    return flattened


def find_json_ld_type(objects: list[dict[str, Any]], type_name: str) -> dict[str, Any] | None:
    for item in flatten_json_ld(objects):
        item_type = item.get("@type")
        if item_type == type_name or (isinstance(item_type, list) and type_name in item_type):
            return item
    return None


def get_meta(soup: BeautifulSoup, *, property_name: str | None = None, name: str | None = None) -> str | None:
    if property_name:
        element = soup.select_one(f'meta[property="{property_name}"]')
    elif name:
        element = soup.select_one(f'meta[name="{name}"]')
    else:
        return None
    if not element:
        return None
    content = element.get("content")
    return clean_text(content) if isinstance(content, str) else None


def parse_price_text(value: Any) -> int | None:
    text = clean_text(value)
    match = PRICE_RE.search(text)
    if not match:
        # JSON-LD values sometimes contain digits without currency suffix.
        if re.fullmatch(r"\d+(?:[.,]\d+)?", text):
            return int(float(text.replace(",", ".")))
        return None
    digits = re.sub(r"\D", "", match.group(1))
    return int(digits) if digits else None


def first_price_from_selectors(soup: BeautifulSoup, selectors: Iterable[str]) -> int | None:
    for selector in selectors:
        for element in soup.select(selector):
            value = parse_price_text(element.get_text(" ", strip=True))
            if value is not None:
                return value
    return None


def extract_prices(soup: BeautifulSoup, product_json: dict[str, Any] | None) -> dict[str, int | None]:
    list_price: int | None = None
    sale_price: int | None = None
    build_pc_price: int | None = None
    regional_price: int | None = None

    if product_json:
        offers = product_json.get("offers")
        offer = offers[0] if isinstance(offers, list) and offers else offers
        if isinstance(offer, dict):
            sale_price = parse_price_text(offer.get("price") or offer.get("lowPrice"))
            list_price = parse_price_text(offer.get("highPrice") or offer.get("listPrice"))

    sale_price = sale_price or first_price_from_selectors(
        soup,
        (
            ".product-detail-price",
            ".product-price",
            ".price",
            ".price-new",
            ".sale-price",
            "[class*='price-sale']",
            "[class*='price_new']",
        ),
    )
    list_price = list_price or first_price_from_selectors(
        soup,
        (
            ".old-price",
            ".price-old",
            "[class*='price-old']",
            "[class*='market-price']",
        ),
    )

    page_text = clean_text(soup.get_text(" ", strip=True))
    for label, target in (
        ("giá build pc", "build_pc_price"),
        ("giá buildpc", "build_pc_price"),
        ("giá khu vực", "regional_price"),
    ):
        match = re.search(rf"{re.escape(label)}\s*[:\-]?\s*([^|]{{0,80}})", fold_text(page_text), re.IGNORECASE)
        if not match:
            continue
        parsed = parse_price_text(match.group(1))
        if target == "build_pc_price":
            build_pc_price = build_pc_price or parsed
        else:
            regional_price = regional_price or parsed

    return {
        "list_price": list_price,
        "sale_price": sale_price,
        "build_pc_price": build_pc_price,
        "regional_price": regional_price,
    }


def add_spec(specs: dict[str, str], key: Any, value: Any) -> None:
    clean_key = clean_text(key).rstrip(":")
    clean_value = clean_text(value)
    if not clean_key or not clean_value or clean_key == clean_value:
        return
    if len(clean_key) > 180 or len(clean_value) > 3000:
        return
    specs.setdefault(clean_key, clean_value)


def valid_spec_pair(key: str, value: str) -> bool:
    if not key or not value or key == value:
        return False
    folded_key = fold_text(key)
    if folded_key in {"thong so ky thuat", "thong so san pham", "chi tiet thong so", "san pham"}:
        return False
    return len(key) <= 180 and len(value) <= 3000


def parse_specs_from_container(container: Tag) -> dict[str, str]:
    specs: dict[str, str] = {}
    for row in container.select("tr"):
        cells = row.find_all(["th", "td"], recursive=False)
        if len(cells) >= 2:
            add_spec(specs, cells[0].get_text(" ", strip=True), " ".join(cell.get_text(" ", strip=True) for cell in cells[1:]))
    for definition_list in container.select("dl"):
        terms = definition_list.find_all("dt")
        for term in terms:
            value = term.find_next_sibling("dd")
            if value:
                add_spec(specs, term.get_text(" ", strip=True), value.get_text(" ", strip=True))
    # Conservative generic rows: exactly two direct text-bearing children.
    for row in container.select("li, .item, .spec-item, .attribute-item, .parameter-item"):
        nested_rows = row.select(":scope li")
        if nested_rows:
            continue
        children = [child for child in row.find_all(recursive=False) if isinstance(child, Tag)]
        if len(children) < 2:
            continue
        key = children[0].get_text(" ", strip=True)
        value = " ".join(child.get_text(" ", strip=True) for child in children[1:])
        if valid_spec_pair(clean_text(key), clean_text(value)):
            add_spec(specs, key, value)
    return specs


def find_spec_containers(soup: BeautifulSoup) -> list[Tag]:
    heading_patterns = (
        "thong so ky thuat",
        "thong so san pham",
        "chi tiet thong so",
        "specification",
    )
    containers: list[Tag] = []
    seen: set[int] = set()
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "div", "span"]):
        folded = fold_text(heading.get_text(" ", strip=True))
        if not any(pattern in folded for pattern in heading_patterns):
            continue
        current: Tag | None = heading
        for _ in range(5):
            if current is None:
                break
            if current.select("table, dl, li, .spec-item, .attribute-item, .parameter-item"):
                identifier = id(current)
                if identifier not in seen:
                    containers.append(current)
                    seen.add(identifier)
                break
            parent = current.parent
            current = parent if isinstance(parent, Tag) else None
    # Known classes as fallback.
    for selector in (
        ".product-spec",
        ".product-specification",
        ".specifications",
        ".specification",
        ".parameter",
        ".parameter__list",
        "[class*='specification']",
        "[class*='parameter']",
    ):
        for container in soup.select(selector):
            identifier = id(container)
            if identifier not in seen:
                containers.append(container)
                seen.add(identifier)
    return containers


def extract_raw_specs(soup: BeautifulSoup) -> dict[str, str]:
    specs: dict[str, str] = {}
    for container in find_spec_containers(soup):
        parsed = parse_specs_from_container(container)
        for key, value in parsed.items():
            specs.setdefault(key, value)
    # Table fallback only when no scoped specs were found.
    if not specs:
        for table in soup.select("table"):
            parsed = parse_specs_from_container(table)
            if len(parsed) >= 3:
                for key, value in parsed.items():
                    specs.setdefault(key, value)
    return specs


def extract_breadcrumbs(soup: BeautifulSoup, json_ld_objects: list[dict[str, Any]]) -> list[str]:
    breadcrumb_json = find_json_ld_type(json_ld_objects, "BreadcrumbList")
    output: list[str] = []
    if breadcrumb_json:
        items = breadcrumb_json.get("itemListElement")
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    name = item.get("name")
                    if name:
                        output.append(clean_text(name))
    if output:
        return deduplicate(output)
    for selector in (".breadcrumb a", "ol.breadcrumb a", ".breadcrumbs a", "[class*='breadcrumb'] a"):
        for anchor in soup.select(selector):
            text = anchor.get_text(" ", strip=True)
            if text:
                output.append(clean_text(text))
    return deduplicate(output)


def extract_images(soup: BeautifulSoup, product_json: dict[str, Any] | None) -> list[str]:
    urls: list[str] = []
    if product_json:
        image = product_json.get("image")
        if isinstance(image, str):
            urls.append(image)
        elif isinstance(image, list):
            urls.extend(item for item in image if isinstance(item, str))
    og_image = get_meta(soup, property_name="og:image")
    if og_image:
        urls.append(og_image)
    for image in soup.select("img[src], img[data-src]"):
        src = image.get("src") or image.get("data-src")
        if isinstance(src, str) and ("product" in src.lower() or "upload" in src.lower()):
            urls.append(urljoin(BASE_URL, src))
    return deduplicate(urls)[:20]


def extract_name(soup: BeautifulSoup, product_json: dict[str, Any] | None) -> str:
    if product_json and product_json.get("name"):
        return clean_text(product_json["name"])
    heading = soup.select_one("h1")
    if heading:
        return clean_text(heading.get_text(" ", strip=True))
    return get_meta(soup, property_name="og:title") or ""


def extract_brand(soup: BeautifulSoup, product_json: dict[str, Any] | None, name: str) -> str | None:
    if product_json:
        raw = product_json.get("brand")
        if isinstance(raw, dict):
            raw = raw.get("name")
        if isinstance(raw, list):
            raw = raw[0] if raw else None
        if raw:
            return clean_text(raw).strip("[]'\"") or None
    for label in ("Thương hiệu", "Hãng sản xuất", "Brand"):
        match = re.search(rf"{re.escape(label)}\s*[:\-]?\s*([A-Za-z0-9 ._-]+)", soup.get_text(" ", strip=True), re.IGNORECASE)
        if match:
            return clean_text(match.group(1))
    first_token = clean_text(name).split(" ")[0] if name else ""
    return first_token or None


def extract_sku(soup: BeautifulSoup, product_json: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if product_json:
        sku = product_json.get("sku") or product_json.get("mpn")
        product_id = product_json.get("productID") or product_json.get("productId")
        if sku or product_id:
            return (clean_text(sku) or None, clean_text(product_id) or None)
    text = soup.get_text(" ", strip=True)
    for pattern in (
        r"Mã\s*(?:SP|sản phẩm)\s*[:\-]?\s*([A-Za-z0-9._-]+)",
        r"SKU\s*[:\-]?\s*([A-Za-z0-9._-]+)",
    ):
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(1)), None
    return None, None


def extract_stock(soup: BeautifulSoup) -> dict[str, Any]:
    text = fold_text(soup.get_text(" ", strip=True))
    if "het hang" in text or "tam het hang" in text:
        return {"status": "out_of_stock", "quantity": None}
    if "lien he" in text and "con hang" not in text:
        return {"status": "contact", "quantity": None}
    if "con hang" in text or "san hang" in text:
        return {"status": "in_stock", "quantity": None}
    return {"status": "unknown", "quantity": None}


def extract_warranty(soup: BeautifulSoup) -> str | None:
    text = clean_text(soup.get_text(" ", strip=True))
    matches = re.findall(r"[^.。!?]{0,90}bảo hành[^.。!?]{0,160}", text, flags=re.IGNORECASE)
    if not matches:
        return None
    return clean_text(matches[0])[:350]


def extract_description(soup: BeautifulSoup, product_json: dict[str, Any] | None) -> str | None:
    if product_json and product_json.get("description"):
        return clean_text(product_json.get("description"))
    return get_meta(soup, name="description") or get_meta(soup, property_name="og:description")


def spec_map(raw_specs: dict[str, str]) -> dict[str, str]:
    return {fold_text(key): clean_text(value) for key, value in raw_specs.items()}


def pick_spec(raw_specs: dict[str, str], *aliases: str) -> str | None:
    mapped = spec_map(raw_specs)
    for alias in aliases:
        folded_alias = fold_text(alias)
        for key, value in mapped.items():
            if key == folded_alias or folded_alias in key:
                return value
    return None


def first_number(value: Any) -> float | None:
    match = NUMBER_RE.search(clean_text(value))
    return float(match.group().replace(",", ".")) if match else None


def extract_numbers(value: Any) -> list[float]:
    return [float(item.replace(",", ".")) for item in NUMBER_RE.findall(clean_text(value))]


def parse_capacity_gb(value: Any) -> int | None:
    text = fold_text(value)
    number = first_number(text)
    if number is None:
        return None
    if "tb" in text:
        return int(round(number * 1024))
    if "gb" in text:
        return int(round(number))
    if "mb" in text:
        return int(round(number / 1024))
    return None


def parse_resolution(value: Any) -> tuple[int | None, int | None, str | None]:
    text = clean_text(value)
    match = re.search(r"(\d{3,5})\s*[x×]\s*(\d{3,5})", text, re.IGNORECASE)
    width = int(match.group(1)) if match else None
    height = int(match.group(2)) if match else None
    folded = fold_text(text)
    label = None
    if "4k" in folded or (width and width >= 3800):
        label = "4K"
    elif "3k" in folded:
        label = "3K"
    elif "2k" in folded or "qhd" in folded or (width and width >= 2500):
        label = "2K"
    elif "full hd" in folded or "fhd" in folded or (width and width >= 1900):
        label = "Full HD"
    elif "hd" in folded:
        label = "HD"
    return width, height, label


def parse_bool(value: Any) -> bool | None:
    text = fold_text(value)
    if not text:
        return None
    if any(token in text for token in ("khong", "no ", "none")):
        return False
    if any(token in text for token in ("co", "yes", "ho tro", "included")):
        return True
    return None


def parse_form_factors(value: Any) -> list[str]:
    text = fold_text(value)
    output: list[str] = []
    patterns = {
        "E-ATX": ("e-atx", "eatx"),
        "ATX": ("atx",),
        "Micro-ATX": ("micro-atx", "micro atx", "matx", "m-atx"),
        "Mini-ITX": ("mini-itx", "mini itx", "itx"),
    }
    for label, aliases in patterns.items():
        if any(alias in text for alias in aliases):
            output.append(label)
    # E-ATX contains ATX; keep both because a case supporting E-ATX usually supports ATX.
    return deduplicate(output)


def parse_connectivity(value: Any) -> list[str]:
    text = fold_text(value)
    output: list[str] = []
    if "2.4" in text or "wireless" in text:
        output.append("2.4ghz")
    if "bluetooth" in text or "bt" in text:
        output.append("bluetooth")
    if "usb" in text or "co day" in text or "wired" in text:
        output.append("wired")
    return deduplicate(output)


def parse_ports(value: Any) -> list[str]:
    text = clean_text(value)
    known = ("HDMI", "DisplayPort", "DP", "USB-C", "Type-C", "USB-A", "DVI", "VGA", "3.5 mm", "Thunderbolt")
    output = [item for item in known if item.lower() in text.lower()]
    return deduplicate(output)


def normalize_cpu(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    socket = pick_spec(raw_specs, "Socket", "Socket CPU")
    return compact_dict({
        "manufacturer": "AMD" if "amd" in fold_text(text) or "ryzen" in fold_text(text) else "Intel" if "intel" in fold_text(text) or "core" in fold_text(text) else None,
        "model": clean_text(name),
        "socket": clean_text(socket) or parse_socket(text),
        "cores": int(first_number(pick_spec(raw_specs, "Số nhân", "Cores")) or 0) or None,
        "threads": int(first_number(pick_spec(raw_specs, "Số luồng", "Threads")) or 0) or None,
        "base_clock_ghz": first_number(pick_spec(raw_specs, "Xung nhịp cơ bản", "Base clock", "Tốc độ CPU")),
        "boost_clock_ghz": parse_max_ghz(pick_spec(raw_specs, "Turbo", "Boost", "Tốc độ tối đa", "Tốc độ CPU")),
        "tdp_w": int(first_number(pick_spec(raw_specs, "TDP", "Công suất", "Default TDP")) or 0) or None,
        "has_integrated_gpu": parse_bool(pick_spec(raw_specs, "Đồ họa tích hợp", "Integrated Graphics", "iGPU")),
        "included_cooler": parse_bool(pick_spec(raw_specs, "Tản nhiệt đi kèm", "Cooler Included")),
    })


def normalize_mainboard(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "socket": clean_text(pick_spec(raw_specs, "Socket", "Socket CPU")) or parse_socket(text),
        "chipset": clean_text(pick_spec(raw_specs, "Chipset")) or parse_chipset(text),
        "form_factor": clean_text(pick_spec(raw_specs, "Kích thước", "Form Factor", "Chuẩn mainboard")) or first_or_none(parse_form_factors(text)),
        "ram_type": parse_ram_type(pick_spec(raw_specs, "Loại RAM", "Chuẩn RAM", "Memory Type") or text),
        "ram_slots": int(first_number(pick_spec(raw_specs, "Số khe RAM", "RAM Slots")) or 0) or None,
        "max_ram_gb": parse_capacity_gb(pick_spec(raw_specs, "RAM tối đa", "Hỗ trợ RAM tối đa", "Max Memory")),
        "m2_slots": int(first_number(pick_spec(raw_specs, "Khe M.2", "M.2 Slots")) or 0) or None,
        "sata_ports": int(first_number(pick_spec(raw_specs, "SATA", "SATA Ports")) or 0) or None,
        "wifi": parse_bool(pick_spec(raw_specs, "Wi-Fi", "Wifi", "Wireless")),
        "bluetooth": parse_bool(pick_spec(raw_specs, "Bluetooth")),
    })


def normalize_ram(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    capacity = parse_capacity_gb(pick_spec(raw_specs, "Dung lượng", "Capacity", "RAM") or text)
    module_count = parse_module_count(text)
    return compact_dict({
        "ram_type": parse_ram_type(pick_spec(raw_specs, "Loại RAM", "Memory Type", "Chuẩn RAM") or text),
        "capacity_gb": capacity,
        "module_count": module_count,
        "capacity_per_module_gb": int(capacity / module_count) if capacity and module_count else None,
        "speed_mhz": int(first_number(pick_spec(raw_specs, "Bus", "Tốc độ Bus RAM", "Speed") or parse_mhz_text(text)) or 0) or None,
        "cas_latency": int(first_number(pick_spec(raw_specs, "CAS", "CL")) or 0) or None,
    })


def normalize_gpu(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "gpu_model": parse_gpu_model(text),
        "vram_gb": parse_vram_gb(text),
        "vram_type": parse_vram_type(text),
        "length_mm": int(first_number(pick_spec(raw_specs, "Chiều dài", "Kích thước", "Dimensions")) or 0) or None,
        "slot_width": first_number(pick_spec(raw_specs, "Độ dày", "Slots", "Slot")),
        "tdp_w": int(first_number(pick_spec(raw_specs, "TDP", "Công suất tiêu thụ", "Power Consumption")) or 0) or None,
        "recommended_psu_w": int(first_number(pick_spec(raw_specs, "Nguồn đề xuất", "Recommended PSU", "PSU đề nghị")) or 0) or None,
        "power_connectors": split_list(pick_spec(raw_specs, "Nguồn phụ", "Power Connectors")),
        "display_outputs": parse_ports(pick_spec(raw_specs, "Cổng kết nối", "Output", "Display Outputs")),
    })


def normalize_ssd(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "capacity_gb": parse_capacity_gb(pick_spec(raw_specs, "Dung lượng", "Capacity") or text),
        "form_factor": clean_text(pick_spec(raw_specs, "Form Factor", "Kích thước")) or parse_ssd_form_factor(text),
        "interface": clean_text(pick_spec(raw_specs, "Chuẩn giao tiếp", "Interface")) or parse_ssd_interface(text),
        "protocol": "NVMe" if "nvme" in fold_text(text) else "SATA" if "sata" in fold_text(text) else None,
        "read_speed_mbps": int(first_number(pick_spec(raw_specs, "Tốc độ đọc", "Read Speed")) or 0) or None,
        "write_speed_mbps": int(first_number(pick_spec(raw_specs, "Tốc độ ghi", "Write Speed")) or 0) or None,
    })


def normalize_psu(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "wattage_w": parse_wattage(text),
        "efficiency_rating": parse_80_plus(text),
        "standard": clean_text(pick_spec(raw_specs, "Chuẩn nguồn", "Standard")) or ("ATX" if "atx" in fold_text(text) else None),
        "modular_type": parse_modular_type(text),
        "pcie_connectors": split_list(pick_spec(raw_specs, "PCI-E", "PCIe", "Cổng PCIe")),
        "has_12vhpwr": "12vhpwr" in fold_text(text) or "12v-2x6" in fold_text(text),
    })


def normalize_case(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "supported_mainboard_form_factors": parse_form_factors(pick_spec(raw_specs, "Mainboard hỗ trợ", "Hỗ trợ mainboard", "Motherboard Support") or text),
        "max_gpu_length_mm": int(first_number(pick_spec(raw_specs, "Chiều dài VGA tối đa", "Max VGA Length", "GPU Length")) or 0) or None,
        "max_cpu_cooler_height_mm": int(first_number(pick_spec(raw_specs, "Chiều cao tản nhiệt CPU", "CPU Cooler Height")) or 0) or None,
        "supported_radiator_sizes_mm": parse_radiator_sizes(pick_spec(raw_specs, "Radiator", "Hỗ trợ tản nhiệt nước") or text),
        "included_fans": int(first_number(pick_spec(raw_specs, "Quạt đi kèm", "Included Fans")) or 0) or None,
        "psu_standard": "ATX" if "atx" in fold_text(text) else None,
    })


def normalize_cooler(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    folded = fold_text(text)
    return compact_dict({
        "cooler_type": "aio" if "aio" in folded or "tan nhiet nuoc" in folded else "air",
        "supported_sockets": parse_sockets(text),
        "height_mm": int(first_number(pick_spec(raw_specs, "Chiều cao", "Height")) or 0) or None,
        "radiator_size_mm": first_or_none(parse_radiator_sizes(text)),
        "rated_tdp_w": int(first_number(pick_spec(raw_specs, "TDP", "Công suất tản nhiệt")) or 0) or None,
    })


def normalize_monitor(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    width, height, label = parse_resolution(pick_spec(raw_specs, "Độ phân giải", "Resolution") or text)
    return compact_dict({
        "screen_inches": first_number(pick_spec(raw_specs, "Kích thước", "Kích thước màn hình", "Screen Size") or parse_inches_text(text)),
        "resolution_width": width,
        "resolution_height": height,
        "resolution_label": label,
        "refresh_rate_hz": int(first_number(pick_spec(raw_specs, "Tần số quét", "Refresh Rate") or parse_hz_text(text)) or 0) or None,
        "panel_type": parse_panel_type(pick_spec(raw_specs, "Tấm nền", "Panel") or text),
        "response_time_ms": first_number(pick_spec(raw_specs, "Thời gian phản hồi", "Response Time")),
        "brightness_nits": int(first_number(pick_spec(raw_specs, "Độ sáng", "Brightness")) or 0) or None,
        "adaptive_sync": parse_adaptive_sync(text),
        "ports": parse_ports(pick_spec(raw_specs, "Cổng kết nối", "Ports") or text),
        "vesa_mount": clean_text(pick_spec(raw_specs, "VESA", "Treo tường")) or None,
    })


def normalize_keyboard(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "layout": parse_keyboard_layout(text),
        "switch_type": clean_text(pick_spec(raw_specs, "Switch", "Loại switch")) or None,
        "connectivity": parse_connectivity(pick_spec(raw_specs, "Kết nối", "Connectivity") or text),
        "hot_swappable": parse_bool(pick_spec(raw_specs, "Hot swap", "Hotswap")) if pick_spec(raw_specs, "Hot swap", "Hotswap") else ("hot swap" in fold_text(text) or "hotswap" in fold_text(text)),
        "rgb": "rgb" in fold_text(text),
        "keycap_material": clean_text(pick_spec(raw_specs, "Keycap", "Chất liệu keycap")) or None,
    })


def normalize_mouse(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    return compact_dict({
        "connectivity": parse_connectivity(pick_spec(raw_specs, "Kết nối", "Connectivity") or text),
        "sensor": clean_text(pick_spec(raw_specs, "Cảm biến", "Sensor")) or None,
        "max_dpi": int(first_number(pick_spec(raw_specs, "DPI", "Độ phân giải")) or 0) or None,
        "weight_g": first_number(pick_spec(raw_specs, "Trọng lượng", "Weight")),
        "shape": clean_text(pick_spec(raw_specs, "Kiểu dáng", "Shape")) or None,
    })


def normalize_laptop(raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    text = f"{name} {' '.join(raw_specs.values())}"
    width, height, label = parse_resolution(pick_spec(raw_specs, "Độ phân giải", "Resolution") or text)
    return compact_dict({
        "cpu_model": clean_text(pick_spec(raw_specs, "CPU", "Công nghệ CPU", "Bộ vi xử lý")) or parse_cpu_model(text),
        "gpu_model": clean_text(pick_spec(raw_specs, "Card màn hình", "GPU", "VGA")) or parse_gpu_model(text),
        "gpu_vram_gb": parse_vram_gb(text),
        "ram_gb": parse_capacity_gb(pick_spec(raw_specs, "RAM") or text),
        "max_ram_gb": parse_capacity_gb(pick_spec(raw_specs, "RAM tối đa", "Hỗ trợ RAM tối đa")),
        "storage_gb": parse_capacity_gb(pick_spec(raw_specs, "Ổ cứng", "SSD", "Dung lượng lưu trữ") or text),
        "screen_inches": first_number(pick_spec(raw_specs, "Kích thước màn hình", "Screen Size") or parse_inches_text(text)),
        "resolution_width": width,
        "resolution_height": height,
        "resolution_label": label,
        "refresh_rate_hz": int(first_number(pick_spec(raw_specs, "Tần số quét", "Refresh Rate") or parse_hz_text(text)) or 0) or None,
        "panel_type": parse_panel_type(pick_spec(raw_specs, "Tấm nền", "Panel") or text),
        "brightness_nits": int(first_number(pick_spec(raw_specs, "Độ sáng", "Brightness")) or 0) or None,
        "weight_kg": parse_weight_kg(pick_spec(raw_specs, "Trọng lượng", "Kích thước", "Weight") or text),
        "battery_wh": first_number(pick_spec(raw_specs, "Pin", "Thông tin Pin", "Battery")),
        "os": clean_text(pick_spec(raw_specs, "Hệ điều hành", "OS")) or None,
        "ports": parse_ports(pick_spec(raw_specs, "Cổng giao tiếp", "Cổng kết nối", "Ports") or text),
    })


def normalize_generic(raw_specs: dict[str, str], _: str) -> dict[str, Any]:
    return {}


NORMALIZERS = {
    "cpu": normalize_cpu,
    "mainboard": normalize_mainboard,
    "ram": normalize_ram,
    "gpu": normalize_gpu,
    "ssd": normalize_ssd,
    "psu": normalize_psu,
    "case": normalize_case,
    "cooler": normalize_cooler,
    "monitor": normalize_monitor,
    "keyboard": normalize_keyboard,
    "mouse": normalize_mouse,
    "laptop": normalize_laptop,
}


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item not in (None, "", [], {})}


def first_or_none(values: list[Any]) -> Any | None:
    return values[0] if values else None


def parse_socket(value: Any) -> str | None:
    text = clean_text(value).upper()
    match = re.search(r"\b(?:AM[345]|LGA\s?\d{3,4}|TRX40|TR4|SP3|SP5)\b", text)
    return match.group(0).replace(" ", "") if match else None


def parse_sockets(value: Any) -> list[str]:
    text = clean_text(value).upper()
    return deduplicate(match.group(0).replace(" ", "") for match in re.finditer(r"\b(?:AM[345]|LGA\s?\d{3,4}|TRX40|TR4|SP3|SP5)\b", text))


def parse_chipset(value: Any) -> str | None:
    text = clean_text(value).upper()
    match = re.search(r"\b(?:A|B|H|X|Z|Q|W)\d{3}[A-Z]?\b", text)
    return match.group(0) if match else None


def parse_ram_type(value: Any) -> str | None:
    text = clean_text(value).upper()
    match = re.search(r"\b(?:LP)?DDR[345]\b", text)
    return match.group(0) if match else None


def parse_module_count(value: Any) -> int | None:
    text = fold_text(value)
    match = re.search(r"(\d+)\s*[x×]\s*\d+\s*gb", text)
    return int(match.group(1)) if match else None


def parse_max_ghz(value: Any) -> float | None:
    text = fold_text(value)
    numbers = [float(item.replace(",", ".")) for item in re.findall(r"(\d+(?:[.,]\d+)?)\s*ghz", text)]
    return max(numbers) if numbers else None


def parse_gpu_model(value: Any) -> str | None:
    text = clean_text(value).upper()
    patterns = (
        r"RTX\s*\d{4}(?:\s*TI|\s*SUPER)?",
        r"GTX\s*\d{3,4}(?:\s*TI|\s*SUPER)?",
        r"RX\s*\d{4}(?:\s*XT|\s*XTX)?",
        r"ARC\s*[A-Z]\d{3}",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r"\s+", " ", match.group(0)).strip()
    return None


def parse_cpu_model(value: Any) -> str | None:
    text = clean_text(value)
    patterns = (
        r"Intel\s+Core\s+(?:Ultra\s+)?[3579]\s*[A-Za-z0-9-]+",
        r"Intel\s+Core\s+i[3579]-?\d{4,5}[A-Za-z]*",
        r"AMD\s+Ryzen\s+[3579]\s+\d{4}[A-Za-z0-9-]*",
        r"Ryzen\s+[3579]\s+\d{4}[A-Za-z0-9-]*",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return clean_text(match.group(0))
    return None


def parse_vram_gb(value: Any) -> int | None:
    text = clean_text(value)
    patterns = (
        r"(?:VRAM|GDDR\dX?)\s*[:\-]?\s*(\d+)\s*GB",
        r"(?:RTX|GTX|RX|ARC)[^,;]{0,40}[,\- ]\s*(\d+)\s*GB",
    )
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return None


def parse_vram_type(value: Any) -> str | None:
    match = re.search(r"\bGDDR\dX?\b", clean_text(value), re.IGNORECASE)
    return match.group(0).upper() if match else None


def parse_wattage(value: Any) -> int | None:
    matches = [int(item) for item in re.findall(r"(?<!\d)(\d{3,4})\s*W\b", clean_text(value), re.IGNORECASE)]
    return max(matches) if matches else None


def parse_80_plus(value: Any) -> str | None:
    text = clean_text(value)
    match = re.search(r"80\s*Plus\s*(?:White|Bronze|Silver|Gold|Platinum|Titanium)?", text, re.IGNORECASE)
    return clean_text(match.group(0)) if match else None


def parse_modular_type(value: Any) -> str | None:
    text = fold_text(value)
    if "full modular" in text or "fully modular" in text:
        return "full_modular"
    if "semi modular" in text:
        return "semi_modular"
    if "non modular" in text or "khong modular" in text:
        return "non_modular"
    return None


def parse_radiator_sizes(value: Any) -> list[int]:
    text = clean_text(value)
    values = [int(item) for item in re.findall(r"(?<!\d)(120|140|240|280|360|420)\s*mm", text, re.IGNORECASE)]
    return sorted(set(values))


def parse_ssd_form_factor(value: Any) -> str | None:
    text = fold_text(value)
    if "m.2" in text or "m2" in text:
        return "M.2 2280" if "2280" in text else "M.2"
    if "2.5" in text:
        return '2.5"'
    return None


def parse_ssd_interface(value: Any) -> str | None:
    text = fold_text(value)
    if "pcie 5" in text or "gen 5" in text:
        return "PCIe 5.0"
    if "pcie 4" in text or "gen 4" in text:
        return "PCIe 4.0"
    if "pcie 3" in text or "gen 3" in text:
        return "PCIe 3.0"
    if "sata" in text:
        return "SATA"
    return None


def parse_panel_type(value: Any) -> str | None:
    text = clean_text(value).upper()
    for item in ("QD-OLED", "OLED", "MINI LED", "IPS", "VA", "TN"):
        if item in text:
            return item
    return None


def parse_adaptive_sync(value: Any) -> list[str]:
    text = fold_text(value)
    output: list[str] = []
    if "freesync" in text:
        output.append("FreeSync")
    if "g-sync" in text or "gsync" in text:
        output.append("G-Sync")
    return output


def parse_keyboard_layout(value: Any) -> str | None:
    text = fold_text(value)
    for item in ("60%", "65%", "68%", "75%", "80%", "87 keys", "tkl", "96%", "98%", "100%", "full size"):
        if item in text:
            return item.upper() if item == "tkl" else item
    return None


def parse_weight_kg(value: Any) -> float | None:
    text = fold_text(value)
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*kg", text)
    if match:
        return float(match.group(1).replace(",", "."))
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*g\b", text)
    if match:
        return float(match.group(1).replace(",", ".")) / 1000
    return None


def parse_inches_text(value: Any) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*(?:inch|\")", clean_text(value), re.IGNORECASE)
    return match.group(1) if match else ""


def parse_hz_text(value: Any) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*hz", clean_text(value), re.IGNORECASE)
    return match.group(1) if match else ""


def parse_mhz_text(value: Any) -> str:
    match = re.search(r"(\d+(?:[.,]\d+)?)\s*mhz", clean_text(value), re.IGNORECASE)
    return match.group(1) if match else ""


def split_list(value: Any) -> list[str]:
    text = clean_text(value)
    if not text:
        return []
    return [item.strip() for item in re.split(r"[,;/|]+", text) if item.strip()]


def normalize_specs(category: str, raw_specs: dict[str, str], name: str) -> dict[str, Any]:
    normalizer = NORMALIZERS.get(category, normalize_generic)
    return normalizer(raw_specs, name)


def evaluate_spec_status(category: str, normalized_specs: dict[str, Any], raw_specs: dict[str, str]) -> tuple[str, list[str]]:
    warnings: list[str] = []
    if not raw_specs:
        warnings.append("raw_specs_empty")
    required = CATEGORY_REQUIRED_FIELDS.get(category, ())
    missing = [field for field in required if field not in normalized_specs]
    if missing:
        warnings.append("missing_normalized_fields:" + ",".join(missing))
    if not raw_specs and not normalized_specs:
        return "missing", warnings
    if missing:
        return "partial", warnings
    return "ok", warnings


def parse_product_html(url: str, html: str, raw_html_path: str | None) -> ProductRecord | None:
    soup = BeautifulSoup(html, "html.parser")
    json_ld_objects = parse_json_ld_objects(soup)
    product_json = find_json_ld_type(json_ld_objects, "Product")
    name = extract_name(soup, product_json)
    if not name:
        return None
    breadcrumbs = extract_breadcrumbs(soup, json_ld_objects)
    raw_specs = extract_raw_specs(soup)
    description = extract_description(soup, product_json)
    category = infer_category(url, name, breadcrumbs, raw_specs.keys(), raw_specs.values())
    # Reject obvious content pages that slipped through sitemap discovery.
    prices = extract_prices(soup, product_json)
    if category == "other" and not product_json and not any(prices.values()) and not raw_specs:
        return None
    sku, product_id = extract_sku(soup, product_json)
    images = extract_images(soup, product_json)
    normalized = normalize_specs(category, raw_specs, name)
    status, warnings = evaluate_spec_status(category, normalized, raw_specs)
    return ProductRecord(
        source="anphatpc",
        source_url=url,
        source_product_id=product_id,
        sku=sku,
        category=category,
        subcategory=breadcrumbs[-2] if len(breadcrumbs) >= 2 else None,
        name=name,
        brand=extract_brand(soup, product_json, name),
        thumbnail_url=images[0] if images else None,
        images=images,
        prices=prices,
        stock=extract_stock(soup),
        warranty=extract_warranty(soup),
        description=description,
        breadcrumbs=breadcrumbs,
        raw_specs=raw_specs,
        normalized_specs=normalized,
        parse_warnings=warnings,
        spec_status=status,
        raw_html_path=raw_html_path,
        crawled_at=now_iso(),
    )


def crawl_details(
    *,
    client: RateLimitedHttpClient,
    input_jsonl: Path,
    output_jsonl: Path,
    cache_dir: Path,
    categories: set[str] | None,
    limit: int | None,
    force_refresh: bool,
) -> None:
    existing_urls = {item.get("source_url") for item in read_jsonl(output_jsonl)}
    candidates = list(read_jsonl(input_jsonl))
    processed = 0
    saved = 0
    for item in candidates:
        url = canonicalize_url(item.get("source_url", ""))
        if not url or url in existing_urls:
            continue
        hint_category = item.get("hint_category", "other")
        if categories and hint_category not in categories and hint_category != "other":
            continue
        if limit is not None and processed >= limit:
            break
        processed += 1
        print(f"[{processed}] product {url}", flush=True)
        cache_path = url_cache_path(cache_dir, url)
        try:
            if cache_path.exists() and not force_refresh:
                html = cache_path.read_text(encoding="utf-8")
                print(f"  -> cache {cache_path}", flush=True)
            else:
                html = client.get_text(url)
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(html, encoding="utf-8")
            record = parse_product_html(url, html, str(cache_path))
            if record is None:
                print("  -> skipped: not recognized as product", flush=True)
                continue
            if categories and record.category not in categories:
                print(f"  -> skipped category={record.category}", flush=True)
                continue
            append_jsonl(output_jsonl, asdict(record))
            saved += 1
            print(f"  -> saved category={record.category} status={record.spec_status} specs={len(record.raw_specs)}", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"  -> ERROR {type(exc).__name__}: {exc}", flush=True)
    print(f"Crawl complete: processed={processed}, saved={saved}", flush=True)


def audit_products(input_jsonl: Path, output_json: Path) -> None:
    total = 0
    category_counts: Counter[str] = Counter()
    status_counts: dict[str, Counter[str]] = defaultdict(Counter)
    raw_key_counts: dict[str, Counter[str]] = defaultdict(Counter)
    raw_key_samples: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    normalized_key_counts: dict[str, Counter[str]] = defaultdict(Counter)
    warning_counts: dict[str, Counter[str]] = defaultdict(Counter)

    for item in read_jsonl(input_jsonl):
        total += 1
        category = item.get("category", "other")
        category_counts[category] += 1
        status_counts[category][item.get("spec_status", "unknown")] += 1
        for key, value in (item.get("raw_specs") or {}).items():
            raw_key_counts[category][key] += 1
            samples = raw_key_samples[category][key]
            if value not in samples and len(samples) < 5:
                samples.append(value)
        for key in (item.get("normalized_specs") or {}):
            normalized_key_counts[category][key] += 1
        for warning in item.get("parse_warnings") or []:
            warning_counts[category][warning] += 1

    payload: dict[str, Any] = {"total_products": total, "categories": {}}
    for category, count in category_counts.most_common():
        payload["categories"][category] = {
            "product_count": count,
            "spec_status": dict(status_counts[category]),
            "warnings": dict(warning_counts[category]),
            "raw_spec_keys": [
                {
                    "key": key,
                    "count": key_count,
                    "coverage_percent": round(key_count / count * 100, 2),
                    "sample_values": raw_key_samples[category][key],
                }
                for key, key_count in raw_key_counts[category].most_common()
            ],
            "normalized_fields": [
                {
                    "field": key,
                    "count": key_count,
                    "coverage_percent": round(key_count / count * 100, 2),
                }
                for key, key_count in normalized_key_counts[category].most_common()
            ],
        }
    write_json(output_json, payload)
    print(f"Audit saved: {output_json.resolve()}", flush=True)


def export_json(input_jsonl: Path, output_json: Path) -> None:
    records = list(read_jsonl(input_jsonl))
    write_json(output_json, records)
    print(f"Exported {len(records)} record(s): {output_json.resolve()}", flush=True)


def load_categories(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    categories = {value.strip() for value in values if value.strip()}
    unknown = categories - set(CATEGORY_ORDER)
    if unknown:
        raise ValueError(f"Unknown categories: {sorted(unknown)}")
    return categories


def create_client(args: argparse.Namespace) -> RateLimitedHttpClient:
    return RateLimitedHttpClient(
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
        user_agent=args.user_agent,
    )



# -----------------------------------------------------------------------------
# LLM normalization + final catalog export
# -----------------------------------------------------------------------------

LLM_NORMALIZED_FIELDS: dict[str, Any] = {
    "category": "other",
    "product_type": None,
    "brand": None,
    "model": None,
    "cpu_model": None,
    "cpu_cores": None,
    "cpu_threads": None,
    "cpu_base_clock_ghz": None,
    "cpu_boost_clock_ghz": None,
    "socket": None,
    "ram_gb": None,
    "ram_type": None,
    "ram_speed_mhz": None,
    "max_ram_gb": None,
    "ram_slots": None,
    "ram_standard": None,
    "storage_gb": None,
    "storage_type": None,
    "storage_detail": None,
    "upgrade_storage_options": [],
    "gpu_model": None,
    "gpu_vram_gb": None,
    "gpu_vram_type": None,
    "chipset": None,
    "form_factor": None,
    "psu_wattage_w": None,
    "recommended_psu_w": None,
    "supported_mainboard_form_factors": [],
    "max_gpu_length_mm": None,
    "max_cpu_cooler_height_mm": None,
    "screen_inches": None,
    "resolution_label": None,
    "resolution_width": None,
    "resolution_height": None,
    "refresh_rate_hz": None,
    "panel_type": None,
    "connectivity": [],
    "switch_type": None,
    "layout": None,
    "mouse_dpi": None,
    "os": None,
    "ports": [],
    "weight_kg": None,
    "warranty_months": None,
    "confidence": 0.0,
    "warnings": [],
}

LLM_VALID_CATEGORIES = {
    "desktop_pc",
    "laptop",
    "cpu",
    "mainboard",
    "ram",
    "gpu",
    "ssd",
    "hdd",
    "psu",
    "case",
    "cooler",
    "monitor",
    "keyboard",
    "mouse",
    "headset",
    "webcam",
    "ups",
    "other",
}


def make_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        text = clean_text(value)
        return [text] if text else []
    return [value]


def to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    match = NUMBER_RE.search(clean_text(value))
    return int(float(match.group().replace(",", "."))) if match else None


def to_float_or_none(value: Any) -> float | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = NUMBER_RE.search(clean_text(value))
    return float(match.group().replace(",", ".")) if match else None


def normalize_llm_schema(data: dict[str, Any], fallback_category: str = "other") -> dict[str, Any]:
    """Normalize one LLM JSON object into a stable dict without pydantic.

    This keeps the script Python 3.9 friendly and avoids eval_type_backport issues.
    """
    output: dict[str, Any] = {}

    for key, default in LLM_NORMALIZED_FIELDS.items():
        if isinstance(default, list):
            output[key] = make_list(data.get(key))
        else:
            output[key] = data.get(key, default)

    category = clean_text(output.get("category")) or fallback_category or "other"
    if category not in LLM_VALID_CATEGORIES:
        category = fallback_category if fallback_category in LLM_VALID_CATEGORIES else "other"
    output["category"] = category

    string_fields = [
        "product_type",
        "brand",
        "model",
        "cpu_model",
        "socket",
        "ram_type",
        "ram_standard",
        "storage_type",
        "storage_detail",
        "gpu_model",
        "gpu_vram_type",
        "chipset",
        "form_factor",
        "resolution_label",
        "panel_type",
        "switch_type",
        "layout",
        "os",
    ]
    for key in string_fields:
        value = output.get(key)
        output[key] = clean_text(value) if value not in (None, "") else None

    int_fields = [
        "cpu_cores",
        "cpu_threads",
        "ram_gb",
        "ram_speed_mhz",
        "max_ram_gb",
        "ram_slots",
        "storage_gb",
        "gpu_vram_gb",
        "psu_wattage_w",
        "recommended_psu_w",
        "max_gpu_length_mm",
        "max_cpu_cooler_height_mm",
        "resolution_width",
        "resolution_height",
        "refresh_rate_hz",
        "mouse_dpi",
        "warranty_months",
    ]
    for key in int_fields:
        output[key] = to_int_or_none(output.get(key))

    float_fields = [
        "cpu_base_clock_ghz",
        "cpu_boost_clock_ghz",
        "screen_inches",
        "weight_kg",
        "confidence",
    ]
    for key in float_fields:
        output[key] = to_float_or_none(output.get(key))

    if output.get("confidence") is None:
        output["confidence"] = 0.0
    output["confidence"] = max(0.0, min(1.0, float(output["confidence"])))

    list_string_fields = [
        "upgrade_storage_options",
        "supported_mainboard_form_factors",
        "connectivity",
        "ports",
        "warnings",
    ]
    for key in list_string_fields:
        output[key] = [
            clean_text(item)
            for item in make_list(output.get(key))
            if clean_text(item)
        ]

    return output


def extract_llm_json(text: str) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        value = json.loads(text)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)
    if not match:
        raise ValueError("No JSON object found in LLM response")

    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("LLM response JSON is not an object")
    return value


def compact_product_for_llm(product: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_url": product.get("source_url"),
        "category": product.get("category"),
        "name": product.get("name"),
        "brand": product.get("brand"),
        "description": product.get("description"),
        "raw_specs": product.get("raw_specs") or {},
        "prices": product.get("prices") or {},
    }


def build_llm_prompt(product: dict[str, Any]) -> str:
    payload = compact_product_for_llm(product)

    return f"""
Bạn là bộ chuẩn hóa dữ liệu sản phẩm máy tính, laptop, linh kiện PC và phụ kiện.

Nhiệm vụ:
- Đọc name, category, description, raw_specs.
- Trả về đúng 1 JSON object theo schema bên dưới.
- Không bịa thông tin.
- Nếu không chắc, để null.
- Không trả markdown.
- Không giải thích.
- Chỉ trả JSON hợp lệ.

Quy tắc quan trọng:
- Với RAM dạng "1 x 8GB" thì ram_gb = 8.
- Với RAM dạng "2 x 16GB" thì ram_gb = 32.
- Với "8GB DDR4 (2 khe tối đa 32GB)" thì ram_gb = 8, max_ram_gb = 32, ram_slots = 2.
- Với ổ cứng "1TB HDD (nâng cấp: 1 x M.2 NVMe)" thì storage_gb = 1024, storage_type = "HDD", upgrade_storage_options = ["1 x M.2 NVMe"].
- Với "256GB SSD" thì storage_gb = 256, storage_type = "SSD".
- Với "1TB HDD + 256GB SSD" thì storage_gb = 1280, storage_type = "SSD+HDD".
- 1TB = 1024GB.
- "Liên hệ" không phải giá.
- Với CPU text có "6 Cores 6 Threads" thì cpu_cores = 6, cpu_threads = 6.
- Với "4 nhân 4 luồng" thì cpu_cores = 4, cpu_threads = 4.
- Với GPU "NVIDIA GT730 2GB" thì gpu_model = "NVIDIA GT730", gpu_vram_gb = 2.
- Với GPU tích hợp như "Intel HD Graphics 630" thì gpu_model giữ nguyên, gpu_vram_gb = null.
- Với hệ điều hành, giữ nguyên text nhưng sửa lỗi rõ ràng như "Windows 10 P ro" -> "Windows 10 Pro".
- Nếu description và raw_specs mâu thuẫn, ưu tiên raw_specs và ghi mâu thuẫn vào warnings.
- confidence từ 0 đến 1.
- warnings ghi các điểm không chắc hoặc thiếu dữ liệu quan trọng.

Schema JSON bắt buộc:
{{
  "category": "desktop_pc | laptop | cpu | mainboard | ram | gpu | ssd | hdd | psu | case | cooler | monitor | keyboard | mouse | headset | webcam | ups | other",
  "product_type": string|null,
  "brand": string|null,
  "model": string|null,

  "cpu_model": string|null,
  "cpu_cores": number|null,
  "cpu_threads": number|null,
  "cpu_base_clock_ghz": number|null,
  "cpu_boost_clock_ghz": number|null,
  "socket": string|null,

  "ram_gb": number|null,
  "ram_type": string|null,
  "ram_speed_mhz": number|null,
  "max_ram_gb": number|null,
  "ram_slots": number|null,
  "ram_standard": string|null,

  "storage_gb": number|null,
  "storage_type": string|null,
  "storage_detail": string|null,
  "upgrade_storage_options": string[],

  "gpu_model": string|null,
  "gpu_vram_gb": number|null,
  "gpu_vram_type": string|null,

  "chipset": string|null,
  "form_factor": string|null,
  "psu_wattage_w": number|null,
  "recommended_psu_w": number|null,
  "supported_mainboard_form_factors": string[],
  "max_gpu_length_mm": number|null,
  "max_cpu_cooler_height_mm": number|null,

  "screen_inches": number|null,
  "resolution_label": string|null,
  "resolution_width": number|null,
  "resolution_height": number|null,
  "refresh_rate_hz": number|null,
  "panel_type": string|null,

  "connectivity": string[],
  "switch_type": string|null,
  "layout": string|null,
  "mouse_dpi": number|null,

  "os": string|null,
  "ports": string[],
  "weight_kg": number|null,
  "warranty_months": number|null,

  "confidence": number,
  "warnings": string[]
}}

Dữ liệu sản phẩm:
{json.dumps(payload, ensure_ascii=False, indent=2)}
""".strip()


def validate_llm_specs(item: dict[str, Any]) -> list[str]:
    warnings: list[str] = []

    specs = item.get("llm_normalized_specs") or {}
    raw_specs = item.get("raw_specs") or {}
    category = specs.get("category") or item.get("category")

    if category in {"desktop_pc", "laptop"}:
        if not specs.get("cpu_model"):
            warnings.append("missing_cpu_model")
        if not specs.get("ram_gb"):
            warnings.append("missing_ram_gb")
        if not specs.get("storage_gb"):
            warnings.append("missing_storage_gb")

    if category == "cpu" and not (specs.get("cpu_model") or specs.get("model")):
        warnings.append("missing_cpu_model")

    if category == "gpu" and not specs.get("gpu_model"):
        warnings.append("missing_gpu_model")

    if category == "monitor" and not specs.get("screen_inches"):
        warnings.append("missing_screen_inches")

    ram_gb = specs.get("ram_gb")
    if isinstance(ram_gb, (int, float)):
        if ram_gb <= 0:
            warnings.append("ram_invalid")
        if ram_gb > 512:
            warnings.append("ram_too_large")

    storage_gb = specs.get("storage_gb")
    if isinstance(storage_gb, (int, float)):
        if storage_gb <= 0:
            warnings.append("storage_invalid")
        if storage_gb > 100_000:
            warnings.append("storage_too_large")

    confidence = specs.get("confidence", 0)
    if not isinstance(confidence, (int, float)) or confidence < 0.7:
        warnings.append("low_confidence")

    if raw_specs and not specs:
        warnings.append("llm_empty_specs")

    return warnings


def build_llm_client(base_url: str | None = None, api_key: str | None = None) -> Any:
    try:
        from openai import OpenAI  # type: ignore
    except ImportError as exc:
        raise RuntimeError("Missing dependency: run `uv add openai`") from exc

    return OpenAI(
        base_url=base_url or os.getenv("LLM_BASE_URL", "http://100.78.74.3:8080/v1"),
        api_key=api_key or os.getenv("LLM_API_KEY", "sk-8be590474b9bcda2-5juvfn-a83a8ea6"),
    )


def llm_normalize_one(
    client: Any,
    product: dict[str, Any],
    *,
    model: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    prompt = build_llm_prompt(product)
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            stream = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": "Bạn chỉ trả về JSON hợp lệ. Không markdown. Không giải thích.",
                    },
                    {
                        "role": "user",
                        "content": prompt,
                    },
                ],
                temperature=0.0,
                stream=True,
            )

            content_parts: list[str] = []

            for chunk in stream:
                if not chunk.choices:
                    continue

                delta = chunk.choices[0].delta

                piece = getattr(delta, "content", None)

                if piece:
                    content_parts.append(piece)

            content = "".join(content_parts).strip()
            if not content or "{" not in content:
                print(f"  -> LLM response: {content[:200]!r}", flush=True)
            data = extract_llm_json(content)
            normalized = normalize_llm_schema(
                data,
                fallback_category=clean_text(product.get("category")) or "other",
            )

            output = dict(product)
            output["llm_normalized_specs"] = normalized
            output["llm_normalized_at"] = now_iso()
            output["llm_validation_warnings"] = validate_llm_specs(output)
            return output

        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt >= max_retries:
                break
            wait_time = 1.5 * (attempt + 1)
            print(
                f"  -> retry {attempt + 1}/{max_retries} after {wait_time:.1f}s: {exc}",
                flush=True,
            )
            time.sleep(wait_time)

    failed = dict(product)
    failed["llm_normalized_specs"] = None
    failed["llm_error"] = str(last_error)
    failed["llm_validation_warnings"] = ["llm_failed"]
    return failed


def load_products_json(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of products")

    return [item for item in data if isinstance(item, dict)]


def read_done_urls(path: Path) -> set[str]:
    done: set[str] = set()
    for item in read_jsonl(path):
        url = item.get("source_url")
        if url:
            done.add(url)
    return done


def print_llm_summary(output_jsonl: Path) -> None:
    items = list(read_jsonl(output_jsonl))
    total = len(items)
    failed = 0
    warning_count = 0
    category_counts: Counter[str] = Counter()

    for item in items:
        specs = item.get("llm_normalized_specs")
        if not specs:
            failed += 1
            continue
        category_counts[specs.get("category") or item.get("category") or "other"] += 1
        if item.get("llm_validation_warnings"):
            warning_count += 1

    print("\n===== LLM SUMMARY =====", flush=True)
    print(f"Total: {total}", flush=True)
    print(f"Failed: {failed}", flush=True)
    print(f"With validation warnings: {warning_count}", flush=True)
    print("Categories:", flush=True)
    for category, count in category_counts.most_common():
        print(f"  - {category}: {count}", flush=True)


def llm_normalize_file(
    *,
    input_json: Path,
    output_jsonl: Path,
    output_json: Path,
    final_json: Path | None,
    model: str,
    limit: int | None,
    categories: set[str] | None,
    force: bool,
) -> None:
    products = load_products_json(input_json)

    if categories:
        products = [
            product
            for product in products
            if product.get("category") in categories
        ]

    if limit is not None:
        products = products[:limit]

    if force:
        output_jsonl.unlink(missing_ok=True)
        output_json.unlink(missing_ok=True)
        if final_json:
            final_json.unlink(missing_ok=True)

    done_urls = read_done_urls(output_jsonl)
    client = build_llm_client()

    print(f"LLM input: {input_json}", flush=True)
    print(f"LLM output JSONL: {output_jsonl}", flush=True)
    print(f"LLM output JSON: {output_json}", flush=True)
    print(f"LLM model: {model}", flush=True)
    print(f"Products: {len(products)}", flush=True)

    for index, product in enumerate(products, start=1):
        url = product.get("source_url")
        name = product.get("name")

        if url in done_urls:
            print(f"[{index}/{len(products)}] skip done: {name}", flush=True)
            continue

        print(f"[{index}/{len(products)}] normalize: {name}", flush=True)
        item = llm_normalize_one(client, product, model=model)
        append_jsonl(output_jsonl, item)

        specs = item.get("llm_normalized_specs")
        warnings = item.get("llm_validation_warnings") or []

        if not specs:
            print(f"  -> failed: {item.get('llm_error')}", flush=True)
            continue

        print(
            "  -> ok "
            f"category={specs.get('category')} "
            f"cpu={specs.get('cpu_model')} "
            f"ram={specs.get('ram_gb')} "
            f"storage={specs.get('storage_gb')} "
            f"type={specs.get('storage_type')} "
            f"confidence={specs.get('confidence')} "
            f"warnings={warnings}",
            flush=True,
        )

    llm_records = list(read_jsonl(output_jsonl))
    write_json(output_json, llm_records)
    print(f"Saved LLM JSON: {output_json.resolve()}", flush=True)
    print(f"Saved LLM JSONL: {output_jsonl.resolve()}", flush=True)
    print_llm_summary(output_jsonl)

    if final_json:
        build_final_catalog(
            input_json=output_json,
            output_json=final_json,
        )


def build_final_catalog(*, input_json: Path, output_json: Path) -> None:
    products = load_products_json(input_json)
    final_products: list[dict[str, Any]] = []

    for product in products:
        llm_specs = product.get("llm_normalized_specs")
        if not isinstance(llm_specs, dict):
            continue

        item = {
            "source": product.get("source"),
            "source_url": product.get("source_url"),
            "source_product_id": product.get("source_product_id"),
            "sku": product.get("sku"),
            "category": llm_specs.get("category") or product.get("category"),
            "subcategory": product.get("subcategory"),
            "name": product.get("name"),
            "brand": product.get("brand") or llm_specs.get("brand"),
            "thumbnail_url": product.get("thumbnail_url"),
            "images": product.get("images") or [],
            "prices": product.get("prices") or {},
            "stock": product.get("stock") or {},
            "warranty": product.get("warranty"),
            "description": product.get("description"),
            "breadcrumbs": product.get("breadcrumbs") or [],
            "raw_specs": product.get("raw_specs") or {},
            # Final catalog uses the LLM result as the single normalized_specs field.
            "normalized_specs": llm_specs,
            "validation_warnings": product.get("llm_validation_warnings") or [],
            "llm_warnings": llm_specs.get("warnings") or [],
            "raw_html_path": product.get("raw_html_path"),
            "crawled_at": product.get("crawled_at"),
            "normalized_at": product.get("llm_normalized_at"),
        }
        final_products.append(item)

    write_json(output_json, final_products)
    print(
        f"Saved final catalog: {output_json.resolve()} "
        f"({len(final_products)} product(s))",
        flush=True,
    )


def load_categories(values: list[str] | None) -> set[str] | None:
    if not values:
        return None
    categories = {value.strip() for value in values if value.strip()}
    unknown = categories - set(CATEGORY_ORDER)
    if unknown:
        raise ValueError(f"Unknown categories: {sorted(unknown)}")
    return categories


def create_client(args: argparse.Namespace) -> RateLimitedHttpClient:
    return RateLimitedHttpClient(
        delay_seconds=args.delay,
        timeout_seconds=args.timeout,
        retries=args.retries,
        user_agent=args.user_agent,
    )


def add_llm_args(command: argparse.ArgumentParser) -> None:
    command.add_argument(
        "--llm-model",
        "--model",
        dest="llm_model",
        default=os.getenv("LLM_MODEL", "unsloth/gemma-4-12B-it-qat-GGUF:UD-Q4_K_XL"),
        help="OpenAI-compatible model name for LLM normalization",
    )
    command.add_argument(
        "--llm-limit",
        type=int,
        default=None,
        help="Only normalize first N products",
    )
    command.add_argument(
        "--force-llm",
        "--force",
        dest="force_llm",
        action="store_true",
        help="Overwrite LLM normalized outputs",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "An Phát PC catalog crawler + LLM normalizer "
            "for sale bot / PC builder"
        )
    )
    parser.add_argument("--data-dir", type=Path, default=Path("data/anphat"))
    parser.add_argument(
        "--delay",
        type=float,
        default=0.1,
        help="Seconds between HTTP requests. Default follows An Phát robots crawl-delay=5.",
    )
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--user-agent", default=DEFAULT_USER_AGENT)

    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser("discover", help="Discover product detail URLs")
    discover.add_argument("--sitemap", action="append", default=[])
    discover.add_argument("--entry-page", action="append", default=[])
    discover.add_argument("--seed-file", type=Path, default=None)
    discover.add_argument("--max-sitemap-urls", type=int, default=None)
    discover.add_argument("--max-pages-per-seed", type=int, default=10)

    crawl = subparsers.add_parser("crawl", help="Crawl product detail pages incrementally")
    crawl.add_argument("--category", action="append", default=[])
    crawl.add_argument("--limit", type=int, default=None)
    crawl.add_argument("--force-refresh", action="store_true")

    audit = subparsers.add_parser("audit", help="Audit raw and regex-normalized schemas")
    export = subparsers.add_parser("export", help="Export raw JSONL records to formatted JSON")

    llm = subparsers.add_parser(
        "llm",
        help="LLM normalize products.json and build products_final.json",
    )
    llm.add_argument("--input", type=Path, default=None)
    llm.add_argument("--output-jsonl", type=Path, default=None)
    llm.add_argument("--output-json", type=Path, default=None)
    llm.add_argument("--final-json", type=Path, default=None)
    llm.add_argument("--category", action="append", default=[])
    add_llm_args(llm)

    final = subparsers.add_parser(
        "final",
        help=(
            "Rebuild products_llm_normalized.json from the jsonl (honoring manual "
            "edits) and then build products_final.json from it."
        ),
    )
    final.add_argument("--input", type=Path, default=None)
    final.add_argument("--output", type=Path, default=None)

    all_cmd = subparsers.add_parser(
        "all",
        help="Discover, crawl, audit, export raw JSON; optionally LLM normalize and build final catalog",
    )
    all_cmd.add_argument("--sitemap", action="append", default=[])
    all_cmd.add_argument("--entry-page", action="append", default=[])
    all_cmd.add_argument("--seed-file", type=Path, default=None)
    all_cmd.add_argument("--max-sitemap-urls", type=int, default=None)
    all_cmd.add_argument("--max-pages-per-seed", type=int, default=10)
    all_cmd.add_argument("--category", action="append", default=[])
    all_cmd.add_argument("--limit", type=int, default=None)
    all_cmd.add_argument("--force-refresh", action="store_true")
    all_cmd.add_argument(
        "--llm",
        action="store_true",
        help="Run LLM normalization after crawling and export products_final.json",
    )
    add_llm_args(all_cmd)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    data_dir: Path = args.data_dir
    discovered_path = data_dir / "discovered_urls.jsonl"
    products_path = data_dir / "products.jsonl"
    audit_path = data_dir / "schema_audit.json"
    export_path = data_dir / "products.json"
    llm_jsonl_path = data_dir / "products_llm_normalized.jsonl"
    llm_json_path = data_dir / "products_llm_normalized.json"
    final_path = data_dir / "products_final.json"
    cache_dir = data_dir / "raw_html"

    if args.command in {"discover", "all"}:
        with create_client(args) as client:
            discover_catalog(
                client=client,
                output_jsonl=discovered_path,
                sitemaps=args.sitemap,
                entry_pages=deduplicate(DEFAULT_ENTRY_PAGES + args.entry_page),
                seed_file=args.seed_file,
                max_sitemap_urls=(
                    args.max_sitemap_urls
                    if args.max_sitemap_urls is not None
                    else getattr(args, "limit", None)
                ),
                max_pages_per_seed=args.max_pages_per_seed,
            )

    if args.command in {"crawl", "all"}:
        with create_client(args) as client:
            crawl_details(
                client=client,
                input_jsonl=discovered_path,
                output_jsonl=products_path,
                cache_dir=cache_dir,
                categories=load_categories(args.category),
                limit=args.limit,
                force_refresh=args.force_refresh,
            )

    if args.command in {"audit", "all"}:
        audit_products(products_path, audit_path)

    if args.command in {"export", "all"}:
        export_json(products_path, export_path)

    if args.command == "llm":
        llm_input = args.input or export_path
        llm_output_jsonl = args.output_jsonl or llm_jsonl_path
        llm_output_json = args.output_json or llm_json_path
        llm_final_json = args.final_json or final_path
        llm_normalize_file(
            input_json=llm_input,
            output_jsonl=llm_output_jsonl,
            output_json=llm_output_json,
            final_json=llm_final_json,
            model=args.llm_model,
            limit=args.llm_limit,
            categories=load_categories(args.category),
            force=args.force_llm,
        )

    if args.command == "final":
        final_input = args.input or llm_json_path
        final_output = args.output or final_path

        # When using the default input, honor manual edits to the jsonl by
        # rebuilding products_llm_normalized.json from the jsonl first so
        # products_final.json reflects the user's curated jsonl state.
        if args.input is None and llm_jsonl_path.exists():
            llm_records = list(read_jsonl(llm_jsonl_path))
            write_json(llm_json_path, llm_records)
            print(
                f"Rebuilt {llm_json_path.name} from {llm_jsonl_path.name}: "
                f"{len(llm_records)} record(s)",
                flush=True,
            )

        build_final_catalog(
            input_json=final_input,
            output_json=final_output,
        )

    if args.command == "all" and args.llm:
        llm_normalize_file(
            input_json=export_path,
            output_jsonl=llm_jsonl_path,
            output_json=llm_json_path,
            final_json=final_path,
            model=args.llm_model,
            limit=args.llm_limit,
            categories=load_categories(args.category),
            force=args.force_llm,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped by user. Existing JSONL files are preserved for resume.", file=sys.stderr)
        raise SystemExit(130)
