"""M5b greedy Build PC algorithm.

Single-pass allocation per category + 1 retry-on-incompatibility + alternatives.
"""
from __future__ import annotations

import logging
from typing import Any

from agents.compat.rules import evaluate as evaluate_compat
from agents.compat.schemas import (
    BuildRequirements,
    CompatibilityResult,
    PCBuild,
    PCComponent,
    Priority,
    UseCase,
)

logger = logging.getLogger("agents.build_pc_algorithm")

BASE_ALLOCATION: dict[UseCase, dict[str, float]] = {
    "gaming":       {"cpu": 0.20, "mobo": 0.12, "ram": 0.10, "gpu": 0.45, "storage": 0.05, "psu": 0.08},
    "office":       {"cpu": 0.30, "mobo": 0.15, "ram": 0.20, "gpu": 0.10, "storage": 0.15, "psu": 0.10},
    "video_editing":{"cpu": 0.35, "mobo": 0.10, "ram": 0.20, "gpu": 0.20, "storage": 0.10, "psu": 0.05},
    "3d_render":    {"cpu": 0.30, "mobo": 0.10, "ram": 0.15, "gpu": 0.30, "storage": 0.10, "psu": 0.05},
    "general":      {"cpu": 0.25, "mobo": 0.15, "ram": 0.20, "gpu": 0.20, "storage": 0.10, "psu": 0.10},
}

PRIORITY_MULTIPLIER: dict[Priority, dict[str, float]] = {
    "performance": {"cpu": 1.10, "mobo": 1.00, "ram": 1.05, "gpu": 1.10, "storage": 1.00, "psu": 1.00},
    "balanced":    {"cpu": 1.00, "mobo": 1.00, "ram": 1.00, "gpu": 1.00, "storage": 1.00, "psu": 1.00},
    "budget":      {"cpu": 0.90, "mobo": 0.95, "ram": 0.90, "gpu": 0.85, "storage": 1.10, "psu": 1.10},
}

CATEGORIES = ["cpu", "mobo", "ram", "gpu", "storage", "psu"]


def category_filter_sql(cat: str) -> str:
    if cat == "cpu":
        return "ps.cpu_cores IS NOT NULL"
    if cat == "mobo":
        return "ps.form_factor IS NOT NULL AND ps.cpu_cores IS NULL"
    if cat == "ram":
        return "ps.ram_gb IS NOT NULL AND ps.psu_wattage_w IS NULL AND ps.cpu_cores IS NULL"
    if cat == "gpu":
        return "ps.gpu_model IS NOT NULL"
    if cat == "storage":
        return "ps.storage_gb IS NOT NULL AND ps.cpu_cores IS NULL AND ps.gpu_model IS NULL"
    if cat == "psu":
        return "ps.psu_wattage_w IS NOT NULL"
    return "FALSE"


def cpu_pref_filter_sql(pref: str) -> str:
    if pref == "intel":
        return " AND (p.brand ILIKE '%intel%' OR ps.socket ILIKE 'LGA%' OR ps.socket ILIKE '%1700%' OR ps.socket ILIKE '%1851%')"
    if pref == "amd":
        return " AND (p.brand ILIKE '%amd%' OR ps.socket ILIKE 'AM%')"
    return ""


def gpu_pref_filter_sql(pref: str) -> str:
    if pref == "nvidia":
        return " AND (p.brand ILIKE '%nvidia%' OR ps.gpu_model ILIKE '%rtx%' OR ps.gpu_model ILIKE '%gtx%')"
    if pref == "amd":
        return " AND (p.brand ILIKE '%amd%' OR ps.gpu_model ILIKE '%rx%' OR ps.gpu_model ILIKE '%radeon%')"
    return ""


_SQL_CANDIDATES = """
SELECT
    p.id, p.slug, p.name, p.brand,
    COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
    ps.cpu_cores, ps.cpu_model, ps.socket,
    ps.ram_gb, ps.ram_type, ps.max_ram_gb,
    ps.gpu_model, ps.gpu_vram_gb,
    ps.psu_wattage_w, ps.recommended_psu_w,
    ps.storage_gb, ps.storage_type,
    ps.form_factor,
    COALESCE(cp.stock_status, p.stock_status) AS stock_status
FROM products p
LEFT JOIN product_specs ps ON ps.product_id = p.id
LEFT JOIN product_current_prices cp ON cp.product_id = p.id
WHERE p.status = 'active' AND p.deleted_at IS NULL
  AND p.source = %s
  AND COALESCE(cp.price_vnd, p.price_vnd) IS NOT NULL
  AND COALESCE(cp.price_vnd, p.price_vnd) > 0
  AND {cat_filter}
  {extra}
ORDER BY price_vnd ASC
LIMIT 20
"""


async def fetch_candidates(
    conn: Any,
    *,
    source: str,
    category: str,
    budget_vnd: int,
    cpu_pref: str = "any",
    gpu_pref: str = "any",
    ram_min_gb: int | None = None,
) -> list[dict[str, Any]]:
    cat_filter = category_filter_sql(category)
    extra = ""
    if category == "cpu":
        extra = cpu_pref_filter_sql(cpu_pref)
    elif category == "gpu":
        extra = gpu_pref_filter_sql(gpu_pref)
    elif category == "ram" and ram_min_gb:
        extra = f" AND ps.ram_gb >= {int(ram_min_gb)}"
    sql = _SQL_CANDIDATES.format(cat_filter=cat_filter, extra=extra)
    async with conn.cursor() as cur:
        await cur.execute(sql, (source,))
        rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = dict(r)
        if d.get("price_vnd") and int(d["price_vnd"]) <= budget_vnd:
            out.append(d)
    return out


def _score_candidate(c: dict[str, Any], use_case: UseCase) -> float:
    match = 0.5
    if use_case == "gaming" and c.get("gpu_vram_gb"):
        match = min(1.0, float(c["gpu_vram_gb"]) / 12.0)
    elif use_case == "video_editing" and c.get("cpu_cores"):
        match = min(1.0, float(c["cpu_cores"]) / 16.0)
    elif use_case == "3d_render" and c.get("gpu_vram_gb"):
        match = min(1.0, float(c["gpu_vram_gb"]) / 16.0)
    elif use_case == "office" and c.get("ram_gb"):
        match = min(1.0, float(c["ram_gb"]) / 32.0)
    price = float(c.get("price_vnd") or 0) or 1.0
    value = min(1.0, 5_000_000.0 / price)
    in_stock = 1.0 if c.get("stock_status") == "in_stock" else 0.5
    return 0.6 * match + 0.3 * value + 0.1 * in_stock


def _pick_top(candidates: list[dict[str, Any]], use_case: UseCase, k: int) -> list[dict[str, Any]]:
    scored = sorted(candidates, key=lambda c: _score_candidate(c, use_case), reverse=True)
    return scored[:k]


def _to_component(c: dict[str, Any], category: str) -> PCComponent:
    return PCComponent(
        category=category,
        product_id=c["id"],
        name=c.get("name") or c["id"],
        price_vnd=int(c.get("price_vnd") or 0),
        url=f"https://anphatpc.com.vn/{c.get('slug')}.html" if c.get("slug") else "",
        pinned=False,
    )


async def _fetch_pinned_items(conn: Any, pinned: dict[str, str], source: str) -> dict[str, dict[str, Any]]:
    if not pinned:
        return {}
    keys = [v for v in pinned.values() if v]
    if not keys:
        return {}
    sql = """
    SELECT
        p.id, p.slug, p.name, p.brand, p.category,
        COALESCE(cp.price_vnd, p.price_vnd) AS price_vnd,
        ps.cpu_cores, ps.cpu_model, ps.socket,
        ps.ram_gb, ps.ram_type, ps.max_ram_gb,
        ps.gpu_model, ps.gpu_vram_gb,
        ps.psu_wattage_w, ps.recommended_psu_w,
        ps.storage_gb, ps.storage_type,
        ps.form_factor,
        ps.supported_mainboard_form_factors, ps.warnings
    FROM products p
    LEFT JOIN product_specs ps ON ps.product_id = p.id
    LEFT JOIN product_current_prices cp ON cp.product_id = p.id
    WHERE p.status = 'active' AND p.deleted_at IS NULL
      AND (p.id = ANY(%s) OR p.slug = ANY(%s))
    """
    async with conn.cursor() as cur:
        await cur.execute(sql, (keys, keys))
        rows = [dict(r) async for r in cur]
    by_id = {r["id"]: r for r in rows}
    by_slug = {r["slug"]: r for r in rows}
    out: dict[str, dict[str, Any]] = {}
    for cat, key in pinned.items():
        item = by_id.get(key) or by_slug.get(key)
        if item:
            out[cat] = item
    return out


async def build_greedy(
    conn: Any,
    req: BuildRequirements,
    *,
    source: str = "anphatpc",
    top_n: int = 10,
    max_alternatives: int = 2,
) -> PCBuild:
    alloc = dict(BASE_ALLOCATION[req.use_case])
    mult = PRIORITY_MULTIPLIER[req.priority]
    for cat in alloc:
        alloc[cat] = alloc[cat] * mult[cat]
    total_alloc = sum(alloc.values()) or 1.0
    alloc = {k: v / total_alloc for k, v in alloc.items()}
    for cat in alloc:
        alloc[cat] = round(alloc[cat], 6)

    pinned_items = await _fetch_pinned_items(conn, req.pinned, source)

    chosen: dict[str, dict[str, Any]] = dict(pinned_items)
    chosen_components: list[PCComponent] = []
    for cat, item in pinned_items.items():
        chosen_components.append(PCComponent(
            category=cat,
            product_id=item["id"],
            name=item.get("name") or item["id"],
            price_vnd=int(item.get("price_vnd") or 0),
            url=f"https://anphatpc.com.vn/{item.get('slug')}.html" if item.get('slug') else "",
            pinned=True,
        ))

    spent = sum(int(i.get("price_vnd") or 0) for i in chosen.values())
    warnings: list[str] = []

    for cat in CATEGORIES:
        if cat in chosen:
            continue
        budget_for_cat = max(0, round(req.budget_vnd * alloc[cat]))
        candidates = await fetch_candidates(
            conn,
            source=source,
            category=cat,
            budget_vnd=budget_for_cat,
            cpu_pref=req.cpu_preference,
            gpu_pref=req.gpu_preference,
            ram_min_gb=req.ram_min_gb,
        )
        if not candidates:
            all_candidates = await fetch_candidates(
                conn,
                source=source,
                category=cat,
                budget_vnd=req.budget_vnd,
                cpu_pref=req.cpu_preference,
                gpu_pref=req.gpu_preference,
                ram_min_gb=req.ram_min_gb,
            )
            if all_candidates:
                pick = min(all_candidates, key=lambda c: int(c.get("price_vnd") or 0))
                warnings.append(f"{cat}: vượt budget, chọn rẻ nhất ({int(pick['price_vnd']):,} VND)")
            else:
                warnings.append(f"{cat}: không tìm thấy sản phẩm")
                continue
        else:
            top = _pick_top(candidates, req.use_case, top_n)
            pick = top[0]
        chosen[cat] = pick
        chosen_components.append(_to_component(pick, cat))
        spent += int(pick.get("price_vnd") or 0)

    compat = evaluate_compat(list(chosen.values()))

    if not compat.compatible:
        retry_chosen, retry_compat = await _retry_swap(conn, chosen, compat, source, req, alloc, req.budget_vnd)
        if retry_compat.compatible or len(retry_compat.issues) < len(compat.issues):
            chosen = retry_chosen
            compat = retry_compat
            chosen_components = [_to_component(c, cat) for cat, c in chosen.items()]
            warnings.append("Đã retry 1 lần để sửa lỗi tương thích")

    total_price = sum(int(c.price_vnd) for c in chosen_components)
    alternatives = await _build_alternatives(
        conn, chosen, req, source, alloc, max_alternatives
    )

    reasoning = _build_reasoning(req, chosen, total_price, req.budget_vnd, compat, warnings)

    return PCBuild(
        build=chosen_components,
        total_price_vnd=total_price,
        compatibility=compat,
        reasoning=reasoning,
        alternatives=alternatives,
    )


async def _retry_swap(
    conn: Any,
    chosen: dict[str, dict[str, Any]],
    compat: CompatibilityResult,
    source: str,
    req: BuildRequirements,
    alloc: dict[str, float],
    budget_vnd: int,
) -> tuple[dict[str, dict[str, Any]], CompatibilityResult]:
    new_chosen = dict(chosen)
    for issue in compat.issues:
        cat = _category_of_issue(issue.pair, new_chosen)
        if cat is None:
            continue
        budget_for_cat = max(0, round(budget_vnd * alloc.get(cat, 0.15)))
        candidates = await fetch_candidates(
            conn, source=source, category=cat, budget_vnd=budget_for_cat,
            cpu_pref=req.cpu_preference, gpu_pref=req.gpu_preference,
            ram_min_gb=req.ram_min_gb,
        )
        if len(candidates) >= 2 and candidates[1]["id"] != new_chosen[cat]["id"]:
            new_chosen[cat] = candidates[1]
    return new_chosen, evaluate_compat(list(new_chosen.values()))


def _category_of_issue(pair: tuple[str, str], chosen: dict[str, dict[str, Any]]) -> str | None:
    _, b_id = pair
    for cat, item in chosen.items():
        if item.get("id") == b_id:
            return cat
    return None


async def _build_alternatives(
    conn: Any,
    chosen: dict[str, dict[str, Any]],
    req: BuildRequirements,
    source: str,
    alloc: dict[str, float],
    max_n: int,
) -> list[PCBuild]:
    alts: list[PCBuild] = []
    for swap_cat in ("gpu", "cpu"):
        if len(alts) >= max_n:
            break
        if swap_cat not in chosen:
            continue
        budget_for_cat = max(0, round(req.budget_vnd * alloc.get(swap_cat, 0.20)))
        cands = await fetch_candidates(
            conn, source=source, category=swap_cat, budget_vnd=budget_for_cat,
            cpu_pref=req.cpu_preference, gpu_pref=req.gpu_preference,
            ram_min_gb=req.ram_min_gb,
        )
        top = _pick_top(cands, req.use_case, 5)
        for alt_pick in top:
            if alt_pick["id"] == chosen[swap_cat]["id"]:
                continue
            alt_chosen = dict(chosen)
            alt_chosen[swap_cat] = alt_pick
            alt_components = [_to_component(c, cat) for cat, c in alt_chosen.items()]
            alt_total = sum(int(c.price_vnd) for c in alt_components)
            alt_compat = evaluate_compat(list(alt_chosen.values()))
            alts.append(PCBuild(
                build=alt_components,
                total_price_vnd=alt_total,
                compatibility=alt_compat,
                reasoning=f"Thay {swap_cat} bằng {alt_pick.get('name')}",
                alternatives=[],
            ))
            break
    return alts


def _build_reasoning(
    req: BuildRequirements,
    chosen: dict[str, dict[str, Any]],
    total_price: int,
    budget: int,
    compat: CompatibilityResult,
    warnings: list[str],
) -> str:
    parts = [
        f"Build {req.use_case} theo priority {req.priority}, tổng {total_price:,} VND / budget {budget:,} VND."
    ]
    if compat.compatible:
        parts.append("Tất cả linh kiện tương thích.")
    else:
        parts.append(f"Có {len(compat.issues)} vấn đề tương thích chưa giải quyết.")
    if compat.warnings:
        parts.append("Lưu ý: " + "; ".join(compat.warnings[:3]))
    if warnings:
        parts.append("Cảnh báo: " + "; ".join(warnings[:3]))
    parts.append("Chọn case và tản nhiệt riêng theo nhu cầu.")
    return " ".join(parts)
