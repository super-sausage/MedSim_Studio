"""Clinical pathology-aware lung nodule parameter sampling.

This module converts a small set of high-level user choices into a complete
lesion parameter set that can be consumed by the existing lesion generator.
The ranges are intentionally guideline-shaped rather than diagnosis-grade:
they align to the practical size buckets used by Fleischner 2017 and
Lung-RADS v2022 so the frontend can drive realistic simulation presets.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Literal, Optional

import numpy as np

NoduleType = Literal["solid", "part_solid", "ggo", "calcified"]
SizeCategory = Literal["micro", "small", "medium", "large"]
RiskLevel = Literal["low", "medium", "high"]


SIZE_RANGES_MM: Dict[SizeCategory, tuple[float, float]] = {
    "micro": (2.0, 5.9),
    "small": (6.0, 7.9),
    "medium": (8.0, 14.9),
    "large": (15.0, 30.0),
}

RISK_BETA_SHAPES: Dict[RiskLevel, tuple[float, float]] = {
    "low": (2.0, 5.0),
    "medium": (2.5, 2.5),
    "high": (5.0, 2.0),
}


@dataclass
class SampledNoduleParameters:
    nodule_type: str
    size_category: str
    risk_level: str
    target_lobe: str
    lesion_type: str
    shape: str
    diameter_mm: float
    radius_x_mm: float
    radius_y_mm: float
    radius_z_mm: float
    hu_mean: float
    hu_std: float
    margin_sharpness: float
    spiculation_degree: float
    lobulation_degree: float
    calcification_fraction: float
    necrosis_fraction: float
    placement_margin_mm: float
    guideline_basis: List[str]
    notes: List[str]

    def to_generator_config(
        self,
        *,
        center_x: float,
        center_y: float,
        center_z: float,
    ) -> Dict[str, Any]:
        return {
            "lesion_type": self.lesion_type,
            "shape": self.shape,
            "center_x": center_x,
            "center_y": center_y,
            "center_z": center_z,
            "radius_x": self.radius_x_mm,
            "radius_y": self.radius_y_mm,
            "radius_z": self.radius_z_mm,
            "hu_mean": self.hu_mean,
            "hu_std": self.hu_std,
            "margin_sharpness": self.margin_sharpness,
            "calcification_fraction": self.calcification_fraction,
            "necrosis_fraction": self.necrosis_fraction,
            "spiculation_degree": self.spiculation_degree,
        }

    def to_response_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["radius_mm"] = [
            float(self.radius_x_mm),
            float(self.radius_y_mm),
            float(self.radius_z_mm),
        ]
        payload.pop("radius_x_mm", None)
        payload.pop("radius_y_mm", None)
        payload.pop("radius_z_mm", None)
        return payload


class PathologyGenerator:
    """Sample clinically shaped lung-nodule parameters."""

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    def sample(
        self,
        *,
        nodule_type: NoduleType,
        size_category: SizeCategory,
        risk_level: RiskLevel,
        target_lobe: str,
    ) -> SampledNoduleParameters:
        diameter_mm = self._sample_diameter_mm(size_category, risk_level)
        radius_x_mm, radius_y_mm, radius_z_mm = self._sample_axis_radii(diameter_mm, nodule_type)
        hu_mean, hu_std = self._sample_hu(nodule_type, risk_level, size_category)
        margin_sharpness = self._sample_margin_sharpness(nodule_type, risk_level)
        spiculation_degree = self._sample_spiculation(nodule_type, risk_level, size_category)
        lobulation_degree = self._sample_lobulation(nodule_type, risk_level)
        calcification_fraction = self._sample_calcification_fraction(nodule_type, risk_level)
        necrosis_fraction = self._sample_necrosis_fraction(
            nodule_type=nodule_type,
            risk_level=risk_level,
            diameter_mm=diameter_mm,
        )
        shape = self._choose_shape(
            nodule_type=nodule_type,
            risk_level=risk_level,
            lobulation_degree=lobulation_degree,
            spiculation_degree=spiculation_degree,
        )
        placement_margin_mm = float(max(3.0, min(12.0, diameter_mm * 0.75)))

        notes = [
            f"{nodule_type} preset with {risk_level} malignancy appearance weighting.",
            f"Diameter sampled inside the {size_category} bucket ({SIZE_RANGES_MM[size_category][0]:.1f}-{SIZE_RANGES_MM[size_category][1]:.1f} mm).",
        ]
        if nodule_type == "calcified":
            notes.append("Calcified pattern drives high HU and near-zero spiculation.")
        elif nodule_type == "ggo":
            notes.append("GGO preset keeps margins softer and HU substantially subsolid.")
        elif nodule_type == "part_solid":
            notes.append("Part-solid preset uses intermediate HU with mixed-margin behavior.")

        return SampledNoduleParameters(
            nodule_type=nodule_type,
            size_category=size_category,
            risk_level=risk_level,
            target_lobe=target_lobe,
            lesion_type="calcification" if nodule_type == "calcified" else "nodule",
            shape=shape,
            diameter_mm=diameter_mm,
            radius_x_mm=radius_x_mm,
            radius_y_mm=radius_y_mm,
            radius_z_mm=radius_z_mm,
            hu_mean=hu_mean,
            hu_std=hu_std,
            margin_sharpness=margin_sharpness,
            spiculation_degree=spiculation_degree,
            lobulation_degree=lobulation_degree,
            calcification_fraction=calcification_fraction,
            necrosis_fraction=necrosis_fraction,
            placement_margin_mm=placement_margin_mm,
            guideline_basis=[
                "Fleischner Society pulmonary nodule recommendations (2017) size thresholds",
                "ACR Lung-RADS assessment categories (v2022) size and composition groupings",
            ],
            notes=notes,
        )

    def _sample_diameter_mm(self, size_category: SizeCategory, risk_level: RiskLevel) -> float:
        low, high = SIZE_RANGES_MM[size_category]
        alpha, beta = RISK_BETA_SHAPES[risk_level]
        ratio = float(self.rng.beta(alpha, beta))
        return round(low + (high - low) * ratio, 2)

    def _sample_axis_radii(self, diameter_mm: float, nodule_type: NoduleType) -> tuple[float, float, float]:
        base_radius = diameter_mm / 2.0
        if nodule_type == "ggo":
            scale_range = (0.90, 1.12)
        elif nodule_type == "calcified":
            scale_range = (0.92, 1.05)
        else:
            scale_range = (0.82, 1.18)
        scales = self.rng.uniform(scale_range[0], scale_range[1], size=3)
        return tuple(round(float(base_radius * scale), 2) for scale in scales)

    def _sample_hu(
        self,
        nodule_type: NoduleType,
        risk_level: RiskLevel,
        size_category: SizeCategory,
    ) -> tuple[float, float]:
        risk_bias = {"low": -1.0, "medium": 0.0, "high": 1.0}[risk_level]
        size_bias = {"micro": -0.3, "small": 0.0, "medium": 0.3, "large": 0.8}[size_category]

        if nodule_type == "solid":
            mean = self.rng.normal(loc=-40 + 45 * risk_bias + 10 * size_bias, scale=35)
            std = self.rng.uniform(25, 65) + 8 * max(risk_bias, 0.0)
        elif nodule_type == "part_solid":
            mean = self.rng.normal(loc=-320 + 70 * risk_bias + 20 * size_bias, scale=60)
            std = self.rng.uniform(60, 120) + 10 * max(risk_bias, 0.0)
        elif nodule_type == "ggo":
            mean = self.rng.normal(loc=-650 + 55 * risk_bias + 25 * size_bias, scale=45)
            std = self.rng.uniform(35, 85)
        else:
            mean = self.rng.normal(loc=380 + 120 * max(risk_bias, 0.0), scale=70)
            std = self.rng.uniform(80, 160)

        return round(float(mean), 1), round(float(max(5.0, std)), 1)

    def _sample_margin_sharpness(self, nodule_type: NoduleType, risk_level: RiskLevel) -> float:
        if nodule_type == "ggo":
            lo, hi = (0.18, 0.48) if risk_level == "low" else (0.28, 0.58)
        elif nodule_type == "part_solid":
            lo, hi = (0.38, 0.72)
        elif nodule_type == "calcified":
            lo, hi = (0.72, 0.94)
        else:
            lo, hi = (0.58, 0.90)
        return round(float(self.rng.uniform(lo, hi)), 3)

    def _sample_spiculation(
        self,
        nodule_type: NoduleType,
        risk_level: RiskLevel,
        size_category: SizeCategory,
    ) -> float:
        if nodule_type == "calcified":
            return round(float(self.rng.uniform(0.0, 0.08)), 3)
        if risk_level == "low":
            ceiling = 0.16 if nodule_type == "solid" else 0.10
            return round(float(self.rng.uniform(0.0, ceiling)), 3)
        if risk_level == "medium":
            floor, ceiling = (0.12, 0.42) if nodule_type != "ggo" else (0.05, 0.25)
            return round(float(self.rng.uniform(floor, ceiling)), 3)

        floor, ceiling = (0.35, 0.82) if nodule_type != "ggo" else (0.18, 0.45)
        if size_category == "large":
            ceiling = min(0.9, ceiling + 0.08)
        return round(float(self.rng.uniform(floor, ceiling)), 3)

    def _sample_lobulation(self, nodule_type: NoduleType, risk_level: RiskLevel) -> float:
        if nodule_type == "ggo":
            bounds = {"low": (0.0, 0.10), "medium": (0.08, 0.25), "high": (0.20, 0.40)}
        elif nodule_type == "calcified":
            bounds = {"low": (0.0, 0.08), "medium": (0.05, 0.18), "high": (0.10, 0.26)}
        else:
            bounds = {"low": (0.02, 0.18), "medium": (0.16, 0.45), "high": (0.34, 0.72)}
        lo, hi = bounds[risk_level]
        return round(float(self.rng.uniform(lo, hi)), 3)

    def _sample_calcification_fraction(self, nodule_type: NoduleType, risk_level: RiskLevel) -> float:
        if nodule_type == "calcified":
            return round(float(self.rng.uniform(0.55, 0.95)), 3)
        if risk_level == "low":
            return round(float(self.rng.uniform(0.0, 0.05)), 3)
        return round(float(self.rng.uniform(0.0, 0.02)), 3)

    def _sample_necrosis_fraction(
        self,
        *,
        nodule_type: NoduleType,
        risk_level: RiskLevel,
        diameter_mm: float,
    ) -> float:
        if nodule_type in {"ggo", "calcified"}:
            return 0.0
        if risk_level != "high" or diameter_mm < 12.0:
            return round(float(self.rng.uniform(0.0, 0.03)), 3)
        return round(float(self.rng.uniform(0.03, 0.16)), 3)

    def _choose_shape(
        self,
        *,
        nodule_type: NoduleType,
        risk_level: RiskLevel,
        lobulation_degree: float,
        spiculation_degree: float,
    ) -> str:
        if nodule_type == "calcified":
            return "spherical"
        if spiculation_degree >= 0.42:
            return "spiculated"
        if lobulation_degree >= 0.38:
            return "lobulated"
        if nodule_type == "ggo":
            return "ellipsoidal" if risk_level == "low" else "irregular"
        if nodule_type == "part_solid":
            return "ellipsoidal" if risk_level == "low" else "lobulated"
        return "spherical" if risk_level == "low" else "ellipsoidal"
