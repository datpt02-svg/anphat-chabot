"""M5b Build PC & Compatibility schemas."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


RuleName = Literal[
    "socket_mismatch",
    "ram_type_mismatch",
    "psu_underpowered",
    "form_factor_mismatch",
    "ram_capacity",
]

PCCategory = Literal["cpu", "mobo", "ram", "gpu", "storage", "psu", "case", "cooler"]

UseCase = Literal["gaming", "office", "video_editing", "3d_render", "general"]
Priority = Literal["performance", "balanced", "budget"]
CPUPref = Literal["intel", "amd", "any"]
GPUPref = Literal["nvidia", "amd", "any"]


class CompatibilityIssue(BaseModel):
    pair: tuple[str, str]
    rule: RuleName
    detail: str
    severity: Literal["error", "warning"] = "error"


class CompatibilityResult(BaseModel):
    compatible: bool
    issues: list[CompatibilityIssue] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    psu_wattage_required: int = 0
    psu_wattage_recommended: int = 0
    total_price_vnd: int = 0
    items: list[dict[str, Any]] = Field(default_factory=list)


class BuildRequirements(BaseModel):
    use_case: UseCase = "general"
    budget_vnd: int = Field(gt=0)
    cpu_preference: CPUPref = "any"
    gpu_preference: GPUPref = "any"
    ram_min_gb: int | None = None
    priority: Priority = "balanced"
    include_overclock: bool = False
    pinned: dict[str, str] = Field(default_factory=dict)


class PCComponent(BaseModel):
    category: PCCategory
    product_id: str
    name: str
    price_vnd: int
    url: str
    pinned: bool = False


class PCBuild(BaseModel):
    build: list[PCComponent]
    total_price_vnd: int
    compatibility: CompatibilityResult
    reasoning: str
    alternatives: list["PCBuild"] = Field(default_factory=list)


class GetGraphNeighborsInput(BaseModel):
    product_id: str
    relation: Literal["compatible_with", "substitutes", "uses_socket", "fits_in", "all"] = "all"
    max_depth: int = Field(default=1, ge=1, le=3)


class GraphNeighbor(BaseModel):
    src: str
    dst: str
    relation: str
    depth: int


PCBuild.model_rebuild()
