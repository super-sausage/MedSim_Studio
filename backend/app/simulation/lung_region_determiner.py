"""Safe candidate placement inside segmented lung lobes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.ndimage import distance_transform_edt


@dataclass
class PlacementResult:
    center_z: float
    center_y: float
    center_x: float
    edge_margin_mm: float
    candidate_count: int
    local_hu_mean: float
    strategy: str

    def as_dict(self) -> dict:
        return {
            "center_voxel_zyx": [self.center_z, self.center_y, self.center_x],
            "edge_margin_mm": self.edge_margin_mm,
            "candidate_count": self.candidate_count,
            "local_hu_mean": self.local_hu_mean,
            "strategy": self.strategy,
        }


class LungRegionDeterminer:
    """Choose a placement center inside a target lobe while avoiding edges/airways."""

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    def find_safe_center(
        self,
        *,
        ct_volume: np.ndarray,
        lobe_mask: np.ndarray,
        spacing: tuple[float, float, float],
        diameter_mm: float,
    ) -> PlacementResult:
        if ct_volume.shape != lobe_mask.shape:
            raise ValueError("ct_volume and lobe_mask must have the same shape")

        lobe_mask = np.asarray(lobe_mask, dtype=bool)
        if not np.any(lobe_mask):
            raise ValueError("target lobe mask is empty")

        edge_distance_mm = distance_transform_edt(lobe_mask, sampling=spacing)
        desired_margin_mm = max(3.0, diameter_mm * 0.75)
        strategies = [
            ("strict", desired_margin_mm, -950.0, -250.0),
            ("balanced", max(2.0, desired_margin_mm * 0.70), -965.0, -180.0),
            ("relaxed", max(1.0, desired_margin_mm * 0.45), -990.0, 120.0),
            ("fallback", 0.0, -1200.0, 300.0),
        ]

        chosen_coords: Optional[np.ndarray] = None
        chosen_strategy = "fallback"
        for strategy_name, margin_mm, hu_low, hu_high in strategies:
            candidate_mask = (
                lobe_mask
                & (edge_distance_mm >= margin_mm)
                & (ct_volume >= hu_low)
                & (ct_volume <= hu_high)
            )
            coords = np.argwhere(candidate_mask)
            if coords.size > 0:
                chosen_coords = coords
                chosen_strategy = strategy_name
                desired_margin_mm = margin_mm
                break

        if chosen_coords is None or chosen_coords.size == 0:
            raise ValueError("could not find any candidate voxel inside the requested lobe")

        centroid = np.mean(np.argwhere(lobe_mask), axis=0)
        sample_limit = min(512, len(chosen_coords))
        if len(chosen_coords) > sample_limit:
            sample_indices = self.rng.choice(len(chosen_coords), size=sample_limit, replace=False)
            sampled_coords = chosen_coords[sample_indices]
        else:
            sampled_coords = chosen_coords

        lobe_span = np.maximum(np.ptp(np.argwhere(lobe_mask), axis=0), 1.0)
        edge_values = edge_distance_mm[
            sampled_coords[:, 0],
            sampled_coords[:, 1],
            sampled_coords[:, 2],
        ]
        hu_values = ct_volume[
            sampled_coords[:, 0],
            sampled_coords[:, 1],
            sampled_coords[:, 2],
        ]
        center_dist = np.linalg.norm((sampled_coords - centroid) / lobe_span, axis=1)
        center_score = 1.0 - np.clip(center_dist / max(float(np.max(center_dist)), 1e-6), 0.0, 1.0)
        edge_score = edge_values / max(float(np.max(edge_values)), 1e-6)
        hu_score = 1.0 - np.clip(np.abs(hu_values + 760.0) / 320.0, 0.0, 1.0)
        score = 0.58 * edge_score + 0.24 * center_score + 0.18 * hu_score

        best_indices = np.argsort(score)[-min(24, len(score)) :]
        weighted = np.exp(score[best_indices] - float(np.max(score[best_indices])))
        weighted = weighted / np.sum(weighted)
        selected_local_index = int(self.rng.choice(best_indices, p=weighted))
        selected = sampled_coords[selected_local_index]

        z, y, x = (int(selected[0]), int(selected[1]), int(selected[2]))
        local_hu_mean = self._local_mean(ct_volume, z, y, x)

        return PlacementResult(
            center_z=float(z),
            center_y=float(y),
            center_x=float(x),
            edge_margin_mm=round(float(edge_distance_mm[z, y, x]), 2),
            candidate_count=int(len(chosen_coords)),
            local_hu_mean=round(local_hu_mean, 2),
            strategy=chosen_strategy,
        )

    @staticmethod
    def _local_mean(volume: np.ndarray, z: int, y: int, x: int) -> float:
        z0, z1 = max(0, z - 1), min(volume.shape[0], z + 2)
        y0, y1 = max(0, y - 1), min(volume.shape[1], y + 2)
        x0, x1 = max(0, x - 1), min(volume.shape[2], x + 2)
        patch = volume[z0:z1, y0:y1, x0:x1]
        return float(np.mean(patch, dtype=np.float64))
