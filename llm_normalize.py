from __future__ import annotations

import argparse
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from openai import OpenAI
from pydantic import BaseModel, Field


DEFAULT_INPUT_PATH = Path("data/anphat/products.json")
DEFAULT_OUTPUT_JSONL = Path("data/anphat/products_llm_normalized.jsonl")
DEFAULT_OUTPUT_JSON = Path("data/anphat/products_llm_normalized.json")


class NormalizedProduct(BaseModel):
    category: str

    # Common
    product_type: str | None = None
    brand: str | None = None
    model: str | None = None

    # CPU / PC / laptop
    cpu_model: str | None = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    cpu_base_clock_ghz: float | None = None
    cpu_boost_clock_ghz: float | None = None
    socket: str | None = None

    # RAM
    ram_gb: int | None = None
    ram_type: str | None = None
    ram_speed_mhz: int | None = None
    max_ram_gb: int | None = None
    ram_slots: int | None = None
    ram_standard: str | None = None

    # Storage
    storage_gb: int | None = None
    storage_type: str | None = None
    storage_detail: str | None = None
    upgrade_storage_options: list[str] = Field(default_factory=list)

    # GPU
    gpu_model: str | None = None
    gpu_vram_gb: int | None = None
    gpu_vram_type: str | None = None

    # Mainboard / case / PSU / builder fields
    chipset: str | None = None
    form_factor: str | None = None
    psu_wattage_w: int | None = None
    recommended_psu_w: int | None = None
    supported_mainboard_form_factors: list[str] = Field(default_factory=list)
    max_gpu_length_mm: int | None = None
    max_cpu_cooler_height_mm: int | None = None

    # Monitor
    screen_inches: float | None = None
    resolution_label: str | None = None
    resolution_width: int | None = None
    resolution_height: int | None = None
    refresh_rate_hz: int | None = None
    panel_type: str | None = None

    # Gear
    connectivity: list[str] = Field(default_factory=list)
    switch_type: str | None = None
    layout: str | None = None
    mouse_dpi: int | None = None

    # Other
    os: str | None = None
    ports: list[str] = Field(default_factory=list)
    weight_kg: float | None = None
    warranty_months: int | None = None

    # LLM quality
    confidence: float = 0.0
    warnings: list[str] = Field(default_factory=list)


def build_client() -> OpenAI:
    base_url = os.getenv("LLM_BASE_URL", "http://localhost:20128/v1")
    api_key = os.getenv("LLM_API_KEY", "EMPTY")

    return OpenAI(
        base_url=base_url,
        api_key=api_key,
    )


def extract_json(text: str) -> dict[str, Any]:
    text = text.strip()

    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{.*\}", text, flags=re.DOTALL)

    if not match:
        raise ValueError("No JSON object found in LLM response")

    return json.loads(match.group(0))


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


def build_prompt(product: dict[str, Any]) -> str:
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

    if category == "cpu":
        if not specs.get("cpu_model") and not specs.get("model"):
            warnings.append("missing_cpu_model")

    if category == "gpu":
        if not specs.get("gpu_model"):
            warnings.append("missing_gpu_model")

    if category == "monitor":
        if not specs.get("screen_inches"):
            warnings.append("missing_screen_inches")

    ram_gb = specs.get("ram_gb")
    if isinstance(ram_gb, int | float):
        if ram_gb <= 0:
            warnings.append("ram_invalid")
        if ram_gb > 512:
            warnings.append("ram_too_large")

    storage_gb = specs.get("storage_gb")
    if isinstance(storage_gb, int | float):
        if storage_gb <= 0:
            warnings.append("storage_invalid")
        if storage_gb > 100_000:
            warnings.append("storage_too_large")

    confidence = specs.get("confidence", 0)
    if not isinstance(confidence, int | float) or confidence < 0.7:
        warnings.append("low_confidence")

    if raw_specs and not specs:
        warnings.append("llm_empty_specs")

    return warnings


def normalize_one(
    client: OpenAI,
    product: dict[str, Any],
    *,
    model: str,
    max_retries: int = 2,
) -> dict[str, Any]:
    prompt = build_prompt(product)

    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            response = client.chat.completions.create(
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
            )

            content = response.choices[0].message.content or ""
            data = extract_json(content)
            normalized = NormalizedProduct.model_validate(data)

            output = dict(product)
            output["llm_normalized_specs"] = normalized.model_dump()
            output["llm_normalized_at"] = time.strftime(
                "%Y-%m-%dT%H:%M:%S%z",
                time.localtime(),
            )
            output["llm_validation_warnings"] = validate_llm_specs(output)

            return output

        except Exception as exc:
            last_error = exc

            if attempt >= max_retries:
                break

            wait_time = 1.5 * (attempt + 1)
            print(f"  -> retry {attempt + 1}/{max_retries} after {wait_time:.1f}s: {exc}")
            time.sleep(wait_time)

    failed = dict(product)
    failed["llm_normalized_specs"] = None
    failed["llm_error"] = str(last_error)
    failed["llm_validation_warnings"] = ["llm_failed"]

    return failed


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(item, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []

    output: list[dict[str, Any]] = []

    with path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            try:
                output.append(json.loads(line))
            except Exception:
                pass

    return output


def read_done_urls(path: Path) -> set[str]:
    done: set[str] = set()

    for item in read_jsonl(path):
        url = item.get("source_url")

        if url:
            done.add(url)

    return done


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_products(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"Input not found: {path}")

    data = json.loads(path.read_text(encoding="utf-8"))

    if not isinstance(data, list):
        raise ValueError("Input JSON must be a list of products")

    return data


def export_json_from_jsonl(jsonl_path: Path, json_path: Path) -> None:
    items = read_jsonl(jsonl_path)
    write_json(json_path, items)


def print_summary(output_jsonl: Path) -> None:
    items = read_jsonl(output_jsonl)

    total = len(items)
    failed = 0
    warning_count = 0
    category_counts: dict[str, int] = {}

    for item in items:
        specs = item.get("llm_normalized_specs")

        if not specs:
            failed += 1
            continue

        category = specs.get("category") or item.get("category") or "other"
        category_counts[category] = category_counts.get(category, 0) + 1

        if item.get("llm_validation_warnings"):
            warning_count += 1

    print()
    print("===== SUMMARY =====")
    print(f"Total: {total}")
    print(f"Failed: {failed}")
    print(f"With validation warnings: {warning_count}")
    print("Categories:")

    for category, count in sorted(category_counts.items(), key=lambda x: x[0]):
        print(f"  - {category}: {count}")


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT_PATH,
    )
    parser.add_argument(
        "--output-jsonl",
        type=Path,
        default=DEFAULT_OUTPUT_JSONL,
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_JSON,
    )
    parser.add_argument(
        "--model",
        default=os.getenv("LLM_MODEL", "ag/gemini-3.5-flash-extra-low"),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-normalize all products and overwrite output files.",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=None,
        help="Only normalize selected category. Can pass multiple times.",
    )

    args = parser.parse_args()

    products = load_products(args.input)

    if args.category:
        allowed = set(args.category)
        products = [
            product
            for product in products
            if product.get("category") in allowed
        ]

    if args.limit is not None:
        products = products[: args.limit]

    if args.force:
        args.output_jsonl.unlink(missing_ok=True)
        args.output_json.unlink(missing_ok=True)

    done_urls = read_done_urls(args.output_jsonl)
    client = build_client()

    print(f"Input: {args.input}")
    print(f"Output JSONL: {args.output_jsonl}")
    print(f"Output JSON: {args.output_json}")
    print(f"Model: {args.model}")
    print(f"Products: {len(products)}")
    print()

    for index, product in enumerate(products, start=1):
        url = product.get("source_url")
        name = product.get("name")

        if url in done_urls:
            print(f"[{index}/{len(products)}] skip done: {name}")
            continue

        print(f"[{index}/{len(products)}] normalize: {name}")

        item = normalize_one(
            client,
            product,
            model=args.model,
        )

        append_jsonl(args.output_jsonl, item)

        specs = item.get("llm_normalized_specs")
        warnings = item.get("llm_validation_warnings") or []

        if not specs:
            print(f"  -> failed: {item.get('llm_error')}")
            continue

        print(
            "  -> ok",
            f"category={specs.get('category')}",
            f"cpu={specs.get('cpu_model')}",
            f"ram={specs.get('ram_gb')}",
            f"storage={specs.get('storage_gb')}",
            f"type={specs.get('storage_type')}",
            f"confidence={specs.get('confidence')}",
            f"warnings={warnings}",
        )

    export_json_from_jsonl(args.output_jsonl, args.output_json)
    print_summary(args.output_jsonl)

    print()
    print(f"Saved JSONL: {args.output_jsonl}")
    print(f"Saved JSON: {args.output_json}")


if __name__ == "__main__":
    main()