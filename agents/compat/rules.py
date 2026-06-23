"""M5b compatibility rules engine.

Hybrid approach:
- 5 hardcoded structural rules (socket, ram_type, psu, form_factor, ram_capacity).
- Data-driven warnings from `product_specs.warnings` JSONB.
"""
from __future__ import annotations

import json
from typing import Any

from agents.compat.schemas import CompatibilityIssue, CompatibilityResult, RuleName


def _is_cpu_like(item: dict[str, Any]) -> bool:
    return bool(item.get("cpu_cores") or item.get("cpu_model"))


def _is_mobo_like(item: dict[str, Any]) -> bool:
    return bool(item.get("form_factor")) and not _is_cpu_like(item)


def _is_ram_like(item: dict[str, Any]) -> bool:
    return bool(item.get("ram_gb")) and not item.get("form_factor") and not item.get("psu_wattage_w")


def _is_psu_like(item: dict[str, Any]) -> bool:
    return bool(item.get("psu_wattage_w"))


def _is_case_like(item: dict[str, Any]) -> bool:
    return bool(item.get("supported_mainboard_form_factors"))


def _safe_load_jsonb(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return None


def _check_socket(cpus: list[dict], mobos: list[dict]) -> list[CompatibilityIssue]:
    issues: list[CompatibilityIssue] = []
    for cpu in cpus:
        for mobo in mobos:
            cpu_sock = cpu.get("socket")
            mobo_sock = mobo.get("socket")
            if cpu_sock and mobo_sock and cpu_sock != mobo_sock:
                issues.append(
                    CompatibilityIssue(
                        pair=(cpu["id"], mobo["id"]),
                        rule="socket_mismatch",
                        detail=f"CPU socket '{cpu_sock}' khác Mobo socket '{mobo_sock}'",
                    )
                )
    return issues


def _check_ram_type(rams: list[dict], mobos: list[dict]) -> list[CompatibilityIssue]:
    issues: list[CompatibilityIssue] = []
    for ram in rams:
        for mobo in mobos:
            ram_t = ram.get("ram_type")
            mobo_t = mobo.get("ram_type")
            if ram_t and mobo_t and ram_t != mobo_t:
                issues.append(
                    CompatibilityIssue(
                        pair=(ram["id"], mobo["id"]),
                        rule="ram_type_mismatch",
                        detail=f"RAM '{ram_t}' không tương thích Mobo '{mobo_t}'",
                    )
                )
    return issues


def _check_ram_capacity(rams: list[dict], mobos: list[dict]) -> list[CompatibilityIssue]:
    issues: list[CompatibilityIssue] = []
    for ram in rams:
        ram_gb = ram.get("ram_gb")
        if not ram_gb:
            continue
        for mobo in mobos:
            max_gb = mobo.get("max_ram_gb")
            if max_gb and ram_gb > max_gb:
                issues.append(
                    CompatibilityIssue(
                        pair=(ram["id"], mobo["id"]),
                        rule="ram_capacity",
                        detail=f"RAM {ram_gb}GB vượt quá max của Mobo ({max_gb}GB)",
                    )
                )
    return issues


def _check_form_factor(mobos: list[dict], cases: list[dict]) -> list[CompatibilityIssue]:
    issues: list[CompatibilityIssue] = []
    for mobo in mobos:
        ff = mobo.get("form_factor")
        if not ff:
            continue
        for case in cases:
            supported = _safe_load_jsonb(case.get("supported_mainboard_form_factors"))
            if supported and ff not in supported:
                issues.append(
                    CompatibilityIssue(
                        pair=(mobo["id"], case["id"]),
                        rule="form_factor_mismatch",
                        detail=f"Mobo form_factor '{ff}' không nằm trong case support ({supported})",
                    )
                )
    return issues


def _check_psu(items: list[dict]) -> tuple[list[CompatibilityIssue], int, int]:
    issues: list[CompatibilityIssue] = []
    psus = [i for i in items if _is_psu_like(i)]
    required_total = sum(int(i.get("recommended_psu_w") or 0) for i in items if i.get("recommended_psu_w"))
    recommended_total = required_total
    if not psus:
        if required_total:
            issues.append(
                CompatibilityIssue(
                    pair=("(build)", "(build)"),
                    rule="psu_underpowered",
                    detail=f"Build cần {required_total}W nhưng không có PSU trong danh sách",
                    severity="warning",
                )
            )
        return issues, required_total, recommended_total
    for psu in psus:
        psu_w = int(psu.get("psu_wattage_w") or 0)
        if psu_w and psu_w < required_total + 100:
            issues.append(
                CompatibilityIssue(
                    pair=("(build)", psu["id"]),
                    rule="psu_underpowered",
                    detail=f"PSU {psu_w}W < yêu cầu {required_total + 100}W (cộng buffer 100W)",
                )
            )
    return issues, required_total, recommended_total


def _collect_data_warnings(items: list[dict]) -> list[str]:
    out: list[str] = []
    for item in items:
        warnings = _safe_load_jsonb(item.get("warnings"))
        if isinstance(warnings, list):
            for w in warnings:
                if isinstance(w, str) and w.strip():
                    out.append(f"[{item.get('id')}] {w}")
        elif isinstance(warnings, str) and warnings.strip():
            out.append(f"[{item.get('id')}] {warnings}")
    return out


def evaluate(items: list[dict[str, Any]]) -> CompatibilityResult:
    if len(items) < 2:
        return CompatibilityResult(
            compatible=False,
            issues=[
                CompatibilityIssue(
                    pair=("", ""),
                    rule="socket_mismatch",
                    detail="Cần ít nhất 2 linh kiện để check tương thích",
                    severity="warning",
                )
            ],
        )

    cpus = [i for i in items if _is_cpu_like(i)]
    mobos = [i for i in items if _is_mobo_like(i)]
    rams = [i for i in items if _is_ram_like(i)]
    cases = [i for i in items if _is_case_like(i)]

    issues: list[CompatibilityIssue] = []
    issues.extend(_check_socket(cpus, mobos))
    issues.extend(_check_ram_type(rams, mobos))
    issues.extend(_check_ram_capacity(rams, mobos))
    issues.extend(_check_form_factor(mobos, cases))
    psu_issues, psu_required, psu_recommended = _check_psu(items)
    issues.extend(psu_issues)

    total_price = sum(int(i.get("price_vnd") or 0) for i in items)
    warnings = _collect_data_warnings(items)

    return CompatibilityResult(
        compatible=not any(i.severity == "error" for i in issues),
        issues=issues,
        warnings=warnings,
        psu_wattage_required=psu_required,
        psu_wattage_recommended=psu_recommended,
        total_price_vnd=total_price,
        items=items,
    )
