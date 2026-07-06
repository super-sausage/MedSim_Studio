"""
Lesion Analyzer
===============

Statistical analysis module for 3D CT lesion volumes.

Provides morphology metrics independent of how the lesion was generated:
  - Volume (mm³)                    — from voxel count × spacing
  - Maximum diameter (mm)            — largest axis of oriented bounding box
  - Mean HU / HU std / HU min/max   — density statistics
  - Surface area (mm²)              — from mask boundary via Marching Cubes
  - Sphericity                      — (π^(1/3) * (6V)^(2/3)) / A

Design
------
- Pure function interface: `analyze(mask, hu_volume, spacing) -> dict`
- No dependency on LesionGenerator or any generation pipeline
- Can be called on ANY lesion mask (voxel, mesh, or mask mode)
"""

import logging
from typing import Dict, Any, Optional, Tuple
import numpy as np

logger = logging.getLogger(__name__)

_HAVE_SKIMAGE = False
try:
    from skimage.measure import marching_cubes, mesh_surface_area
    _HAVE_SKIMAGE = True
except ImportError:
    pass


def analyze(
    lesion_mask: np.ndarray,
    hu_volume: np.ndarray,
    spacing: Optional[Tuple[float, float, float]] = None,
) -> Dict[str, Any]:
    """
    Compute morphological and density statistics for a lesion.

    Args:
        lesion_mask: Binary mask of the lesion (z, y, x), dtype bool or numeric.
        hu_volume:   HU volume from which *lesion_mask* was derived (z, y, x).
                     Can be the same as the lesion volume returned by
                     LesionGenerator — only ``hu_volume[lesion_mask]`` is used.
        spacing:     Voxel spacing (z, y, x) in mm.
                     Falls back to (1, 1, 1) if None.

    Returns:
        dict with keys:
            voxel_count, volume_mm3, max_diameter_mm,
            hu_mean, hu_std, hu_min, hu_max,
            surface_area_mm2, sphericity
            bbox (dict), shape_info (str)
    """
    mask = lesion_mask.astype(bool)
    voxel_count = int(np.sum(mask))

    if voxel_count == 0:
        return {
            "voxel_count": 0,
            "volume_mm3": 0.0,
            "max_diameter_mm": 0.0,
            "hu_mean": 0.0,
            "hu_std": 0.0,
            "hu_min": 0.0,
            "hu_max": 0.0,
            "surface_area_mm2": 0.0,
            "sphericity": 0.0,
            "bbox": {},
            "shape_info": "empty",
        }

    if spacing is None:
        spacing = (1.0, 1.0, 1.0)

    # ── Voxel volume & volume in mm³ ──
    voxel_volume_mm3 = spacing[0] * spacing[1] * spacing[2]
    volume_mm3 = voxel_count * voxel_volume_mm3

    # ── HU statistics ──
    hu_values = hu_volume[mask]
    hu_mean = float(np.mean(hu_values))
    hu_std = float(np.std(hu_values))
    hu_min = float(np.min(hu_values))
    hu_max = float(np.max(hu_values))

    # ── Bounding box ──
    nonzero = np.argwhere(mask)
    bbox = {
        "z_min": int(nonzero[:, 0].min()),
        "z_max": int(nonzero[:, 0].max()),
        "y_min": int(nonzero[:, 1].min()),
        "y_max": int(nonzero[:, 1].max()),
        "x_min": int(nonzero[:, 2].min()),
        "x_max": int(nonzero[:, 2].max()),
    }

    # ── Maximum diameter (largest bounding-box axis in mm) ──
    dz_mm = (bbox["z_max"] - bbox["z_min"]) * spacing[0]
    dy_mm = (bbox["y_max"] - bbox["y_min"]) * spacing[1]
    dx_mm = (bbox["x_max"] - bbox["x_min"]) * spacing[2]
    max_diameter_mm = float(max(dz_mm, dy_mm, dx_mm))

    # ── Surface area & sphericity ──
    surface_area_mm2, sphericity = _compute_surface_metrics(
        mask, spacing, voxel_count, volume_mm3,
    )

    # ── Shape descriptor ──
    # Simple aspect-ratio-based descriptor
    diameters = sorted([dz_mm, dy_mm, dx_mm], reverse=True)
    aspect_ratio = diameters[0] / max(diameters[-1], 1e-6)
    if aspect_ratio < 1.3:
        shape_info = "spherical"
    elif aspect_ratio < 2.0:
        shape_info = "ellipsoidal"
    else:
        shape_info = "elongated"

    result = {
        "voxel_count": voxel_count,
        "volume_mm3": round(volume_mm3, 2),
        "max_diameter_mm": round(max_diameter_mm, 2),
        "diameters_mm": {
            "z": round(dz_mm, 2),
            "y": round(dy_mm, 2),
            "x": round(dx_mm, 2),
        },
        "hu_mean": round(hu_mean, 2),
        "hu_std": round(hu_std, 2),
        "hu_min": round(hu_min, 2),
        "hu_max": round(hu_max, 2),
        "surface_area_mm2": round(surface_area_mm2, 2),
        "sphericity": round(sphericity, 4),
        "bbox": bbox,
        "shape_info": shape_info,
    }

    logger.debug(
        "LesionAnalyzer: voxels=%d volume=%.1fmm³ diameter=%.1fmm "
        "sphericity=%.4f shape=%s",
        voxel_count, volume_mm3, max_diameter_mm, sphericity, shape_info,
    )
    return result


def _compute_surface_metrics(
    mask: np.ndarray,
    spacing: Tuple[float, float, float],
    voxel_count: int,
    volume_mm3: float,
) -> Tuple[float, float]:
    """
    Compute surface area (mm²) and sphericity.

    Uses skimage.measure.marching_cubes when available (fast, accurate).
    Falls back to a voxel-face-counting approximation.
    """
    if _HAVE_SKIMAGE:
        try:
            # skimage marching_cubes requires spacing=(dz, dy, dx) order
            verts, faces, _, _ = marching_cubes(
                mask.astype(float),
                level=0.5,
                spacing=spacing,  # (z, y, x)
                method="lewiner",
            )
            area = mesh_surface_area(verts, faces)
            if area > 0 and voxel_count > 0:
                sphericity = _compute_sphericity(volume_mm3, area)
                return area, sphericity
        except Exception as e:
            logger.debug("marching_cubes failed: %s — falling back", e)

    # Fallback: count exposed voxel faces correctly
    from scipy.ndimage import binary_erosion, generate_binary_structure

    struct = generate_binary_structure(3, 1)  # 6-connected
    eroded = binary_erosion(mask, structure=struct)
    surface_voxels = int(np.sum(mask & (~eroded)))

    # Count exposed faces: for each mask voxel, check if face-neighbor is OUTSIDE
    outside_faces = np.zeros_like(mask, dtype=np.int8)
    # z-direction faces (z+1 / z-1)
    outside_faces[1:] += (mask[1:] & ~mask[:-1]).astype(np.int8)
    outside_faces[:-1] += (mask[:-1] & ~mask[1:]).astype(np.int8)
    # y-direction faces
    outside_faces[:, 1:] += (mask[:, 1:] & ~mask[:, :-1]).astype(np.int8)
    outside_faces[:, :-1] += (mask[:, :-1] & ~mask[:, 1:]).astype(np.int8)
    # x-direction faces
    outside_faces[:, :, 1:] += (mask[:, :, 1:] & ~mask[:, :, :-1]).astype(np.int8)
    outside_faces[:, :, :-1] += (mask[:, :, :-1] & ~mask[:, :, 1:]).astype(np.int8)

    exposed_faces = int(np.sum(outside_faces))

    if exposed_faces == 0:
        return 0.0, 0.0

    # An exposed face has area = product of the 2 non-normal axes
    # z-face (normal=z): spacing[1] * spacing[2]
    # y-face (normal=y): spacing[0] * spacing[2]
    # x-face (normal=x): spacing[0] * spacing[1]
    # Approximate: average face area across all 3 orientations
    avg_face_area = (
        spacing[1] * spacing[2] +
        spacing[0] * spacing[2] +
        spacing[0] * spacing[1]
    ) / 3.0

    area_mm2 = exposed_faces * avg_face_area

    if area_mm2 > 0 and voxel_count > 0:
        sphericity = _compute_sphericity(volume_mm3, area_mm2)
    else:
        sphericity = 0.0

    return area_mm2, sphericity


def _compute_sphericity(volume_mm3: float, surface_area_mm2: float) -> float:
    """
    Sphericity Ψ = π^(1/3) * (6V)^(2/3) / A

    Ψ = 1 for a perfect sphere; < 1 for non-spherical shapes.
    """
    if surface_area_mm2 <= 0:
        return 0.0
    numerator = np.pi ** (1.0 / 3.0) * (6.0 * volume_mm3) ** (2.0 / 3.0)
    return float(numerator / surface_area_mm2)
