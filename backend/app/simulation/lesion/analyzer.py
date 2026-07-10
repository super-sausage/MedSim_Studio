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

Also provides ``extract_mesh()`` for 3D mesh extraction via Marching Cubes,
used by the /preview/lesion-3d API endpoint.

Design
------
- Pure function interface: `analyze(mask, hu_volume, spacing) -> dict`
- No dependency on LesionGenerator or any generation pipeline
- Can be called on ANY lesion mask (voxel, mesh, or mask mode)
"""

import logging
from typing import Dict, Any, List, Optional, Tuple
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


# ---------------------------------------------------------------------------
# 3D Mesh extraction — used by /preview/lesion-3d
# ---------------------------------------------------------------------------


def extract_mesh(
    lesion_mask: np.ndarray,
    spacing: Optional[Tuple[float, float, float]] = None,
) -> Dict[str, Any]:
    """
    Extract a 3D triangle mesh from a binary lesion mask via Marching Cubes.

    The mask should be the raw lesion volume (before writing into a CT
    background) — i.e. the output of ``LesionGenerator.generate_lesion()``
    thresholded with ``!= 0``.

    Args:
        lesion_mask: Binary mask (z, y, x), dtype bool or numeric.
        spacing:     Voxel spacing (z, y, x) in mm. Defaults to (1, 1, 1).

    Returns:
        dict with keys:
            vertices:   List of [x, y, z] vertex positions in mm (physical space)
            faces:      List of [i, j, k] triangle indices
            normals:    List of [nx, ny, nz] per-vertex normals
            bounds:     {min: [x, y, z], max: [x, y, z]} in mm
            center:     [cx, cy, cz] in mm (center of bounding box)
            volume_mm3: Approximate enclosed volume in mm³

    Raises:
        RuntimeError: If scikit-image is not installed or marching_cubes fails.
    """
    if not _HAVE_SKIMAGE:
        raise RuntimeError(
            "scikit-image is required for mesh extraction. "
            "Install with: pip install scikit-image"
        )

    mask = lesion_mask.astype(bool)
    if not mask.any():
        return {
            "vertices": [],
            "faces": [],
            "normals": [],
            "bounds": {"min": [0, 0, 0], "max": [0, 0, 0]},
            "center": [0, 0, 0],
            "volume_mm3": 0.0,
        }

    if spacing is None:
        spacing = (1.0, 1.0, 1.0)

    try:
        # marching_cubes returns (verts, faces, normals, values) in (row, col, depth)
        # order matching our (z, y, x) convention when spacing=(spacing_z, ...)
        verts, faces, normals, _ = marching_cubes(
            mask.astype(float),
            level=0.5,
            spacing=spacing,  # (z, y, x) — matches our convention
            method="lewiner",
        )

        # verts are (N, 3) in (z, y, x) order from skimage.
        # Convert to (x, y, z) for vtk.js / standard graphics convention.
        verts_xyz = verts[:, [2, 1, 0]]  # (z, y, x) → (x, y, z)
        normals_xyz = normals[:, [2, 1, 0]]

        # Bounds in physical (x, y, z)
        v_min = verts_xyz.min(axis=0).tolist()
        v_max = verts_xyz.max(axis=0).tolist()
        center = [
            (v_min[0] + v_max[0]) / 2.0,
            (v_min[1] + v_max[1]) / 2.0,
            (v_min[2] + v_max[2]) / 2.0,
        ]

        # Compute volume via mesh_surface_area is not volume.
        # Approximate enclosed volume from mask voxel count.
        voxel_volume_mm3 = spacing[0] * spacing[1] * spacing[2]
        volume_mm3 = float(np.sum(mask)) * voxel_volume_mm3

        # Deduplicate vertices to reduce payload size
        # (marching_cubes can produce shared vertices — we keep as-is for
        #  simplicity; the frontend Float32Array approach handles duplicates fine)

        result = {
            "vertices": verts_xyz.astype(np.float32).tolist(),
            "faces": faces.astype(np.int32).tolist(),
            "normals": normals_xyz.astype(np.float32).tolist(),
            "bounds": {"min": v_min, "max": v_max},
            "center": center,
            "volume_mm3": round(volume_mm3, 2),
        }

        logger.debug(
            "extract_mesh: %d vertices, %d faces, volume=%.1f mm³",
            len(result["vertices"]),
            len(result["faces"]),
            volume_mm3,
        )
        return result

    except Exception as e:
        logger.exception("Marching cubes mesh extraction failed")
        raise RuntimeError(f"Mesh extraction failed: {e}") from e
