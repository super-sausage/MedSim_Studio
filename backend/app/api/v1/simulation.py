"""
Simulation API

RESTful endpoints for lesion and organ simulation management.
Provides job creation, status tracking, preview generation,
CT phantom generation, and result export for synthetic medical image generation.
"""

import os
import io
import base64
import copy
import uuid
import tempfile
import logging
import threading
from functools import lru_cache
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from PIL import Image

import numpy as np
import pydicom
from scipy.ndimage import zoom

from app.database.session import get_db, SessionLocal
from app.models.simulation import SimulationJob, LesionConfig, OrganConfig
from app.models.dicom import DicomInstance, DicomSeries
from app.schemas.simulation import (
    SimulationJobResponse,
    SimulationJobCreate,
    LesionConfigResponse,
    SimulationPreviewResponse,
    DicomLesionPreviewRequest,
    DicomLesionPreviewResponse,
    DicomLesion3DPreviewRequest,
    DicomLesion3DPreviewResponse,
    DebugLesionRequest,
    DebugLesionResponse,
    LesionAnalysisRequest,
    LesionAnalysisResponse,
    Lesion3DPreviewRequest,
    Lesion3DPreviewResponse,
    LesionInPhantomPreviewRequest,
    LesionInPhantomPreviewResponse,
    CTParamsPreviewRequest,
    CTParamsPreviewResponse,
    PathologyNoduleOnDicomRequest,
    PathologyNoduleOnDicomResponse,
    PathologySampledParameters,
    PathologyPlacementInfo,
    PathologySegmentationLabel,
)
from app.ai.nnunet_lung_lobe import (
    CustomModelNotAvailableError as LungLobeModelNotAvailableError,
    remap_lung_lobe_labels_to_upper_body,
    run_nnunet_lung_lobe,
)
from app.ai.nnunet_lung_lobe.labels import LUNG_LOBE_LABEL_MAP, MODEL_NAME as LUNG_LOBE_MODEL_NAME, get_label_defs as get_lung_lobe_label_defs
from app.simulation.lesion.generator import LesionGenerator
from app.simulation.lesion.analyzer import extract_mesh
from app.simulation.lung_region_determiner import LungRegionDeterminer
from app.simulation.organ.simulator import OrganSimulator
from app.simulation.pathology import PathologyGenerator
from app.simulation.volume_builder import build_volume_from_dicom, build_synthetic_volume
from app.simulation.exporter import export_nrrd, export_nifti, export_dicom_zip
from app.simulation.ct_params_simulator import simulate_ct_scan_params
from app.simulation.phantom_generator import (
    LUNG_SAMPLE_LABEL_MAP,
    LUNG_SEGMENT_LABEL_TO_ID,
    generate_atlas_ct_phantom,
    generate_procedural_ct_phantom,
    list_available_atlas_cases,
    WINDOW_PRESETS,
)
from app.dicom.storage import get_storage_backend

# ── Debug output directory ──
DEBUG_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "debug_output",
)

# Optional matplotlib for debug PNG generation
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Debug visualization helpers ──


def _debug_save_lesion_pngs(
    base_volume: np.ndarray,
    result_volume: np.ndarray,
    lesion_volume: np.ndarray,
    lesion_mask: np.ndarray,
    label: str,
    output_dir: str = DEBUG_OUTPUT_DIR,
) -> None:
    """
    Save 4 debug PNG slices through the lesion center (axial middle slice).

    Files saved:
        lesion_mask_middle_slice.png
        lesion_hu_middle_slice.png
        result_volume_middle_slice.png
        difference_map.png
    """
    if not HAS_MPL:
        logger.warning("matplotlib not installed — skipping debug PNGs")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Find the axial slice at the lesion center
    nonzero = np.argwhere(lesion_mask)
    if len(nonzero) == 0:
        logger.warning("_debug_save_lesion_pngs: lesion_mask is empty, nothing to visualize")
        return
    cz = int(np.median(nonzero[:, 0]))
    cy = int(np.median(nonzero[:, 1]))
    cx = int(np.median(nonzero[:, 2]))

    diff_map = np.abs(result_volume.astype(np.float32) - base_volume.astype(np.float32))

    figures = [
        ("lesion_mask_middle_slice.png", lesion_mask[cz, :, :].astype(np.uint8) * 255,
         "Lesion Mask (axial z={})".format(cz), "gray"),
        ("lesion_hu_middle_slice.png", lesion_volume[cz, :, :],
         "Lesion HU (axial z={})".format(cz), "viridis"),
        ("result_volume_middle_slice.png", result_volume[cz, :, :],
         "Result Volume HU (axial z={})".format(cz), "gray"),
        ("difference_map.png", diff_map[cz, :, :],
         "|Δ HU| (axial z={})".format(cz), "hot"),
    ]

    for fname, data, title, cmap in figures:
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))
        im = ax.imshow(data, cmap=cmap, aspect="equal")
        ax.set_title(title)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        plt.colorbar(im, ax=ax, shrink=0.75)
        path = os.path.join(output_dir, f"{label}_{fname}")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("DEBUG PNG saved: %s", path)


def _debug_log_lesion_write(
    base_volume: np.ndarray,
    result_volume: np.ndarray,
    label: str,
) -> None:
    """Log before/after stats for lesion write step."""
    before = base_volume.astype(np.float32)
    after = result_volume.astype(np.float32)
    delta = after - before
    changed = np.count_nonzero(delta)

    logger.debug(
        "==== LESION WRITE DEBUG [%s] ====\n"
        "  before_mean:  %.2f  before_std:  %.2f\n"
        "  after_mean:   %.2f  after_std:   %.2f\n"
        "  delta_mean:   %.6f  delta_max:   %.2f  delta_min:   %.2f\n"
        "  changed_voxels: %d  (total: %d, ratio: %.6f)",
        label,
        float(np.mean(before)), float(np.std(before)),
        float(np.mean(after)), float(np.std(after)),
        float(np.mean(delta)), float(np.max(delta)), float(np.min(delta)),
        changed, before.size, changed / max(before.size, 1),
    )
    if changed == 0:
        logger.warning("LESION WRITE: changed_voxels == 0 — lesion did NOT modify the volume!")


def _debug_log_position(
    lesion_mask: np.ndarray,
    center: Tuple[float, float, float],
    volume_shape: Tuple[int, int, int],
    label: str,
) -> None:
    """Log position validation for a lesion."""
    nonzero = np.argwhere(lesion_mask)
    if len(nonzero) == 0:
        logger.warning("==== LESION POSITION DEBUG [%s] ==== mask is empty — no position to validate", label)
        return

    bbox = {
        "z_min": int(nonzero[:, 0].min()),
        "z_max": int(nonzero[:, 0].max()),
        "y_min": int(nonzero[:, 1].min()),
        "y_max": int(nonzero[:, 1].max()),
        "x_min": int(nonzero[:, 2].min()),
        "x_max": int(nonzero[:, 2].max()),
    }
    inside = (
        bbox["z_min"] >= 0 and bbox["z_max"] < volume_shape[0]
        and bbox["y_min"] >= 0 and bbox["y_max"] < volume_shape[1]
        and bbox["x_min"] >= 0 and bbox["x_max"] < volume_shape[2]
    )

    logger.debug(
        "==== LESION POSITION DEBUG [%s] ====\n"
        "  volume_shape:  %s\n"
        "  center:        (%.1f, %.1f, %.1f)  (z, y, x)\n"
        "  bbox_z:        [%d, %d]  (size: %d)\n"
        "  bbox_y:        [%d, %d]  (size: %d)\n"
        "  bbox_x:        [%d, %d]  (size: %d)\n"
        "  inside_volume: %s",
        label,
        str(volume_shape),
        center[0], center[1], center[2],
        bbox["z_min"], bbox["z_max"], bbox["z_max"] - bbox["z_min"] + 1,
        bbox["y_min"], bbox["y_max"], bbox["y_max"] - bbox["y_min"] + 1,
        bbox["x_min"], bbox["x_max"], bbox["x_max"] - bbox["x_min"] + 1,
        "YES" if inside else "OUTSIDE",
    )
    if not inside:
        logger.warning("LESION POSITION: lesion is OUTSIDE the volume!")


def _debug_verify_sitk_metadata(
    sitk_image: "sitk.Image",
    nrrd_path: str,
    label: str,
) -> None:
    """
    Verify that SimpleITK preserves origin/spacing/direction after writing.

    Writes the image, reads it back, and compares the metadata.
    This catches coordinate-system corruption during the write round-trip.
    """
    import SimpleITK as _sitk

    # Capture what was SET before write
    orig_origin = sitk_image.GetOrigin()
    orig_spacing = sitk_image.GetSpacing()
    orig_direction = sitk_image.GetDirection()

    # Read back
    try:
        reread = _sitk.ReadImage(nrrd_path)
    except Exception as e:
        logger.error("==== SITK META DEBUG [%s] ==== FAILED to read back: %s", label, e)
        return

    rb_origin = reread.GetOrigin()
    rb_spacing = reread.GetSpacing()
    rb_direction = reread.GetDirection()

    origin_ok = orig_origin == rb_origin
    spacing_ok = orig_spacing == rb_spacing
    direction_ok = orig_direction == rb_direction

    logger.debug(
        "==== SITK META DEBUG [%s] ====\n"
        "  --- Set ---                    --- Read back ---              Match?\n"
        "  Origin:     (%6.2f, %6.2f, %6.2f)    (%6.2f, %6.2f, %6.2f)   %s\n"
        "  Spacing:    (%6.4f, %6.4f, %6.4f)    (%6.4f, %6.4f, %6.4f)   %s\n"
        "  Direction:  (%s)  (%s)  %s",
        label,
        orig_origin[0], orig_origin[1], orig_origin[2],
        rb_origin[0], rb_origin[1], rb_origin[2],
        "OK" if origin_ok else "MISMATCH",
        orig_spacing[0], orig_spacing[1], orig_spacing[2],
        rb_spacing[0], rb_spacing[1], rb_spacing[2],
        "OK" if spacing_ok else "MISMATCH",
        ",".join(f"{v:.4f}" for v in orig_direction),
        ",".join(f"{v:.4f}" for v in rb_direction),
        "OK" if direction_ok else "MISMATCH",
    )

    if not origin_ok:
        logger.warning("SITK META: Origin MISMATCH — coordinate system may be corrupted!")
    if not spacing_ok:
        logger.warning("SITK META: Spacing MISMATCH — voxel dimensions changed!")
    if not direction_ok:
        logger.warning("SITK META: Direction MISMATCH — orientation may have flipped!")

    # Additional: log written file size
    file_size_mb = os.path.getsize(nrrd_path) / (1024 * 1024)
    logger.debug("  File size: %.2f MB", file_size_mb)


def _debug_log_spacing(
    spacing: Optional[Tuple[float, float, float]],
    radius_mm: Tuple[float, float, float],
    label: str,
) -> None:
    """Log spacing-to-voxel conversion diagnostics."""
    if spacing is None:
        logger.debug("==== SPACING DEBUG [%s] ==== spacing=None, using default (1,1,1)", label)
        spacing = (1.0, 1.0, 1.0)

    radius_voxel = (
        radius_mm[0] / spacing[0],  # z
        radius_mm[1] / spacing[1],  # y
        radius_mm[2] / spacing[2],  # x
    )

    logger.debug(
        "==== SPACING DEBUG [%s] ====\n"
        "  spacing (z,y,x):   (%.4f, %.4f, %.4f)\n"
        "  radius_mm (z,y,x): (%.1f, %.1f, %.1f)\n"
        "  radius_voxel:      (%.2f, %.2f, %.2f)\n"
        "  min_voxel_dim:     %.2f\n"
        "  Z compression:     %s (voxel count %.1f — if < 3, lesion is a 2D pancake)",
        label,
        spacing[0], spacing[1], spacing[2],
        radius_mm[0], radius_mm[1], radius_mm[2],
        radius_voxel[0], radius_voxel[1], radius_voxel[2],
        min(radius_voxel),
        "WARNING" if radius_voxel[0] < 3 else "OK",
        radius_voxel[0],
    )
    if radius_voxel[0] < 2:
        logger.warning("SPACING: Z-radius is only %.1f voxels — lesion will be a 2D pancake!", radius_voxel[0])
    if radius_voxel[1] < 2 or radius_voxel[2] < 2:
        logger.warning(
            "SPACING: in-plane radius is < 2 voxels (y=%.1f, x=%.1f) — lesion may be invisible!",
            radius_voxel[1], radius_voxel[2],
        )


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulation", tags=["Simulation"])
_PATHOLOGY_LOBE_CACHE_LOCK = threading.Lock()
_PATHOLOGY_LOBE_CACHE: "OrderedDict[tuple[str, str, tuple[int, int, int]], np.ndarray]" = OrderedDict()
_PATHOLOGY_LOBE_CACHE_MAX_ITEMS = 4
_PATHOLOGY_VOLUME_CACHE_LOCK = threading.Lock()
_PATHOLOGY_VOLUME_CACHE: "OrderedDict[tuple[str, str], tuple[np.ndarray, Dict[str, Any]]]" = OrderedDict()
_PATHOLOGY_VOLUME_CACHE_MAX_ITEMS = 3
_PATHOLOGY_PREVIEW_CACHE_LOCK = threading.Lock()
_PATHOLOGY_PREVIEW_CACHE: "OrderedDict[tuple[Any, ...], Dict[str, Any]]" = OrderedDict()
_PATHOLOGY_PREVIEW_CACHE_MAX_ITEMS = 6
DEFAULT_STANDARDIZED_NOTES = [
    "axis_order = zyx",
    "dtype = float32",
    "spacing order = z,y,x",
    "volume data is stored in top-level simulated_volume_base64",
    "standardized_case is intended for downstream artifact/lesion modules",
]


def _identity_direction_matrix() -> List[List[float]]:
    return [
        [1.0, 0.0, 0.0],
        [0.0, 1.0, 0.0],
        [0.0, 0.0, 1.0],
    ]


def _normalize_origin(origin: Optional[Any]) -> List[float]:
    if isinstance(origin, (list, tuple)) and len(origin) >= 3:
        try:
            return [float(origin[0]), float(origin[1]), float(origin[2])]
        except (TypeError, ValueError):
            pass
    return [0.0, 0.0, 0.0]


def _normalize_direction(direction: Optional[Any]) -> List[List[float]]:
    if isinstance(direction, (list, tuple)):
        if len(direction) == 9:
            try:
                values = [float(v) for v in direction]
                return [values[0:3], values[3:6], values[6:9]]
            except (TypeError, ValueError):
                return _identity_direction_matrix()
        if len(direction) == 3 and all(isinstance(row, (list, tuple)) and len(row) >= 3 for row in direction):
            try:
                return [[float(row[0]), float(row[1]), float(row[2])] for row in direction]
            except (TypeError, ValueError):
                return _identity_direction_matrix()
    return _identity_direction_matrix()


def _normalize_dicom_scan_direction(
    volume: np.ndarray,
    metadata: Dict[str, Any],
    scan_direction: str,
) -> tuple[np.ndarray, Dict[str, Any]]:
    """Normalize DICOM z ordering to the requested head/feet convention."""
    normalized_metadata = dict(metadata)
    direction_matrix = _normalize_direction(normalized_metadata.get("direction"))
    slice_direction = np.asarray(direction_matrix[0], dtype=np.float64)
    slice_direction_norm = float(np.linalg.norm(slice_direction))
    superior_component = 0.0
    if slice_direction_norm > 1e-6:
        slice_direction = slice_direction / slice_direction_norm
        superior_component = float(slice_direction[2])

    normalized_metadata["scan_direction"] = scan_direction
    normalized_metadata["dicom_superior_component"] = superior_component

    # DICOM patient Z increases toward the head (superior).
    # If slice index increases toward superior, z=0 is feet-side and must be flipped
    # for the workspace's default head_to_feet convention.
    natural_is_head_to_feet = superior_component < 0.0
    need_flip = False
    if abs(superior_component) >= 1e-6:
        if scan_direction == "head_to_feet" and not natural_is_head_to_feet:
            need_flip = True
        elif scan_direction == "feet_to_head" and natural_is_head_to_feet:
            need_flip = True

    normalized_metadata["flipped_z"] = need_flip

    if not need_flip:
        return volume, normalized_metadata

    flipped = volume[::-1, :, :].copy()

    origin = _normalize_origin(normalized_metadata.get("origin"))
    z_spacing = float(normalized_metadata.get("spacing", (1.0, 1.0, 1.0))[0])
    if slice_direction_norm > 1e-6 and volume.shape[0] > 1:
        new_origin = (
            np.asarray(origin, dtype=np.float64)
            + slice_direction * z_spacing * float(volume.shape[0] - 1)
        )
        normalized_metadata["origin"] = [float(v) for v in new_origin]

        flipped_direction = [
            [-direction_matrix[0][0], -direction_matrix[0][1], -direction_matrix[0][2]],
            direction_matrix[1],
            direction_matrix[2],
        ]
        normalized_metadata["direction"] = flipped_direction

    return flipped, normalized_metadata


@lru_cache(maxsize=8)
def _get_cached_phantom_payload(
    source: str,
    size: int,
    case_id: str,
    scan_direction: str,
    include_labels: bool,
) -> Dict[str, Any]:
    """Cache expensive phantom generation + base64 encoding by request key."""
    if source == "atlas":
        ct_volume, label_volume, metadata = generate_atlas_ct_phantom(
            case_id=case_id,
            size=size,
            scan_direction=scan_direction,
        )

        response_content = _build_workspace_volume_payload(
            ct_volume,
            metadata,
            label_volume=label_volume if include_labels else None,
            include_labels=include_labels,
        )

        return response_content

    volume, _, metadata = generate_procedural_ct_phantom(size=size)
    return _build_workspace_volume_payload(
        volume,
        metadata,
        include_labels=include_labels,
    )


def _downsample_volume_to_max_dim(
    volume: np.ndarray,
    spacing: tuple[float, float, float],
    max_dim: int,
    *,
    order: int = 1,
) -> tuple[np.ndarray, tuple[float, float, float], float]:
    """Downsample a zyx volume isotropically when it is larger than max_dim."""
    current_max_dim = max(volume.shape)
    if current_max_dim <= max_dim:
        return volume.astype(np.float32, copy=False), spacing, 1.0

    scale = float(max_dim) / float(current_max_dim)
    resized = zoom(volume, (scale, scale, scale), order=order, mode="nearest")
    new_spacing = tuple(float(axis_spacing / scale) for axis_spacing in spacing)
    return resized.astype(np.float32, copy=False), new_spacing, scale


def _resample_label_volume_to_shape(
    label_volume: np.ndarray,
    output_shape: tuple[int, int, int],
) -> np.ndarray:
    """Nearest-neighbor resample a zyx label volume to an exact target shape."""
    if tuple(int(dim) for dim in label_volume.shape) == tuple(int(dim) for dim in output_shape):
        return label_volume.astype(np.uint8, copy=False)

    zoom_factors = tuple(
        float(target_dim) / float(source_dim)
        for source_dim, target_dim in zip(label_volume.shape, output_shape)
    )
    resized = zoom(
        np.asarray(label_volume, dtype=np.float32),
        zoom_factors,
        order=0,
        mode="nearest",
    )
    return np.asarray(np.rint(resized), dtype=np.uint8)


def _get_cached_pathology_lobe_labels(
    cache_key: tuple[str, str, tuple[int, int, int]],
) -> Optional[np.ndarray]:
    with _PATHOLOGY_LOBE_CACHE_LOCK:
        cached = _PATHOLOGY_LOBE_CACHE.get(cache_key)
        if cached is None:
            return None
        _PATHOLOGY_LOBE_CACHE.move_to_end(cache_key)
        return np.array(cached, dtype=np.uint8, copy=True)


def _set_cached_pathology_lobe_labels(
    cache_key: tuple[str, str, tuple[int, int, int]],
    label_volume: np.ndarray,
) -> None:
    with _PATHOLOGY_LOBE_CACHE_LOCK:
        _PATHOLOGY_LOBE_CACHE[cache_key] = np.array(label_volume, dtype=np.uint8, copy=True)
        _PATHOLOGY_LOBE_CACHE.move_to_end(cache_key)
        while len(_PATHOLOGY_LOBE_CACHE) > _PATHOLOGY_LOBE_CACHE_MAX_ITEMS:
            _PATHOLOGY_LOBE_CACHE.popitem(last=False)


def _get_cached_pathology_volume(
    cache_key: tuple[str, str],
) -> Optional[tuple[np.ndarray, Dict[str, Any]]]:
    with _PATHOLOGY_VOLUME_CACHE_LOCK:
        cached = _PATHOLOGY_VOLUME_CACHE.get(cache_key)
        if cached is None:
            return None
        _PATHOLOGY_VOLUME_CACHE.move_to_end(cache_key)
        volume, metadata = cached
        return volume, dict(metadata)


def _set_cached_pathology_volume(
    cache_key: tuple[str, str],
    volume: np.ndarray,
    metadata: Dict[str, Any],
) -> None:
    with _PATHOLOGY_VOLUME_CACHE_LOCK:
        _PATHOLOGY_VOLUME_CACHE[cache_key] = (
            np.array(volume, dtype=np.float32, copy=True),
            dict(metadata),
        )
        _PATHOLOGY_VOLUME_CACHE.move_to_end(cache_key)
        while len(_PATHOLOGY_VOLUME_CACHE) > _PATHOLOGY_VOLUME_CACHE_MAX_ITEMS:
            _PATHOLOGY_VOLUME_CACHE.popitem(last=False)


def _get_cached_pathology_preview_response(
    cache_key: tuple[Any, ...],
) -> Optional[Dict[str, Any]]:
    with _PATHOLOGY_PREVIEW_CACHE_LOCK:
        cached = _PATHOLOGY_PREVIEW_CACHE.get(cache_key)
        if cached is None:
            return None
        _PATHOLOGY_PREVIEW_CACHE.move_to_end(cache_key)
        return copy.deepcopy(cached)


def _set_cached_pathology_preview_response(
    cache_key: tuple[Any, ...],
    payload: Dict[str, Any],
) -> None:
    with _PATHOLOGY_PREVIEW_CACHE_LOCK:
        _PATHOLOGY_PREVIEW_CACHE[cache_key] = copy.deepcopy(payload)
        _PATHOLOGY_PREVIEW_CACHE.move_to_end(cache_key)
        while len(_PATHOLOGY_PREVIEW_CACHE) > _PATHOLOGY_PREVIEW_CACHE_MAX_ITEMS:
            _PATHOLOGY_PREVIEW_CACHE.popitem(last=False)


def _estimate_thoracic_crop_bounds(
    volume: np.ndarray,
) -> tuple[tuple[int, int], tuple[int, int], tuple[int, int]]:
    """Estimate a thoracic ROI so lung-lobe inference keeps native spacing but avoids whole-body scans."""
    volume = np.asarray(volume, dtype=np.float32)
    body_mask = volume > -700.0
    if not np.any(body_mask):
        body_mask = volume > -850.0

    z_idx, y_idx, x_idx = np.where(body_mask)
    if len(z_idx) == 0:
        return (0, volume.shape[0]), (0, volume.shape[1]), (0, volume.shape[2])

    body_bbox = np.zeros_like(body_mask, dtype=bool)
    body_bbox[
        int(z_idx.min()): int(z_idx.max()) + 1,
        int(y_idx.min()): int(y_idx.max()) + 1,
        int(x_idx.min()): int(x_idx.max()) + 1,
    ] = True

    lung_like = (volume >= -980.0) & (volume <= -250.0) & body_bbox
    slice_counts = np.count_nonzero(lung_like, axis=(1, 2))
    min_pixels = max(48, int(volume.shape[1] * volume.shape[2] * 0.002))
    valid_z = np.where(slice_counts >= min_pixels)[0]
    if len(valid_z) == 0:
        return (0, volume.shape[0]), (0, volume.shape[1]), (0, volume.shape[2])

    z_margin = 16
    z0 = max(0, int(valid_z.min()) - z_margin)
    z1 = min(volume.shape[0], int(valid_z.max()) + z_margin + 1)

    cropped_lung_like = lung_like[z0:z1]
    yz_idx = np.where(cropped_lung_like)
    if len(yz_idx[0]) == 0:
        return (z0, z1), (0, volume.shape[1]), (0, volume.shape[2])

    y_margin = 18
    x_margin = 18
    y0 = max(0, int(yz_idx[1].min()) - y_margin)
    y1 = min(volume.shape[1], int(yz_idx[1].max()) + y_margin + 1)
    x0 = max(0, int(yz_idx[2].min()) - x_margin)
    x1 = min(volume.shape[2], int(yz_idx[2].max()) + x_margin + 1)

    return (z0, z1), (y0, y1), (x0, x1)


def _read_dicom_dataset_from_storage(storage: Any, object_key: Optional[str]) -> Optional[Any]:
    if not object_key:
        return None
    dicom_bytes = storage.get_object_bytes(object_key)
    if dicom_bytes is None:
        return None
    try:
        return pydicom.dcmread(io.BytesIO(dicom_bytes), force=True)
    except Exception:
        logger.warning("Failed to parse DICOM object from storage: %s", object_key, exc_info=True)
        return None


def _extract_vector(values: Any, expected_len: int) -> Optional[np.ndarray]:
    if values is None:
        return None
    try:
        vector = np.asarray([float(v) for v in values], dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if vector.shape[0] != expected_len:
        return None
    return vector


def _collect_dicom_ct_slice_geometry(
    storage: Any,
    instances: List[DicomInstance],
) -> tuple[list[Dict[str, Any]], Optional[np.ndarray]]:
    slices: list[Dict[str, Any]] = []
    row_direction: Optional[np.ndarray] = None
    col_direction: Optional[np.ndarray] = None

    for fallback_index, inst in enumerate(instances):
        ds = _read_dicom_dataset_from_storage(storage, inst.pixel_data_path)
        if ds is None:
            continue

        raw_image_position = getattr(ds, "ImagePositionPatient", None)
        if raw_image_position is None:
            raw_image_position = inst.image_position
        raw_image_orientation = getattr(ds, "ImageOrientationPatient", None)
        if raw_image_orientation is None:
            raw_image_orientation = inst.image_orientation

        image_position = _extract_vector(raw_image_position, 3)
        image_orientation = _extract_vector(raw_image_orientation, 6)
        if image_orientation is not None and row_direction is None and col_direction is None:
            row_direction = image_orientation[:3]
            col_direction = image_orientation[3:]

        sort_fallback = inst.slice_location
        if sort_fallback is None:
            sort_fallback = inst.instance_number if inst.instance_number is not None else fallback_index

        slices.append(
            {
                "image_position": image_position,
                "sort_fallback": float(sort_fallback),
            }
        )

    slice_direction: Optional[np.ndarray] = None
    if row_direction is not None and col_direction is not None:
        candidate = np.cross(row_direction, col_direction)
        norm = float(np.linalg.norm(candidate))
        if norm > 1e-6:
            slice_direction = candidate / norm

    if slice_direction is not None and all(item["image_position"] is not None for item in slices):
        for item in slices:
            item["sort_position"] = float(np.dot(item["image_position"], slice_direction))
        slices.sort(key=lambda item: item["sort_position"])
    else:
        slices.sort(key=lambda item: item["sort_fallback"])

    return slices, slice_direction


def _find_dicom_seg_series(db: Session, study_id: str, ct_series_id: str) -> list[DicomSeries]:
    series_items = (
        db.query(DicomSeries)
        .filter(DicomSeries.study_id == study_id)
        .filter(DicomSeries.id != ct_series_id)
        .all()
    )

    candidates: list[DicomSeries] = []
    for series in series_items:
        modality = str(series.modality or "").upper()
        description = str(series.series_description or "").lower()
        protocol = str(series.protocol_name or "").lower()
        if modality == "SEG" or "segmentation" in description or "segmentation" in protocol:
            candidates.append(series)
    return candidates


def _load_dicom_seg_label_volume(
    *,
    db: Session,
    storage: Any,
    study_id: str,
    ct_series_id: str,
    ct_instances: List[DicomInstance],
    need_flip: bool,
    zoom_factor: float,
    output_shape: tuple[int, int, int],
) -> tuple[Optional[np.ndarray], Dict[str, int], Dict[str, list], Optional[str]]:
    """Try converting a same-study DICOM SEG object into a workspace label map."""
    ct_slices, slice_direction = _collect_dicom_ct_slice_geometry(storage, ct_instances)
    if not ct_slices or slice_direction is None:
        return None, {}, {}, None

    ct_positions = [
        float(np.dot(item["image_position"], slice_direction))
        for item in ct_slices
        if item["image_position"] is not None
    ]
    if len(ct_positions) != len(ct_slices):
        return None, {}, {}, None

    for seg_series in _find_dicom_seg_series(db, study_id, ct_series_id):
        seg_instances = (
            db.query(DicomInstance)
            .filter(DicomInstance.series_id == seg_series.id)
            .order_by(DicomInstance.instance_number.asc().nulls_last())
            .all()
        )
        for seg_instance in seg_instances:
            seg_ds = _read_dicom_dataset_from_storage(storage, seg_instance.pixel_data_path)
            if seg_ds is None:
                continue
            try:
                seg_pixels = seg_ds.pixel_array
            except Exception:
                logger.warning("Failed to decode DICOM SEG pixels for series %s", seg_series.id, exc_info=True)
                continue
            if seg_pixels.ndim == 2:
                seg_pixels = seg_pixels[np.newaxis, :, :]

            segment_number_to_id: Dict[int, int] = {}
            for segment in getattr(seg_ds, "SegmentSequence", []):
                segment_number = int(getattr(segment, "SegmentNumber", 0) or 0)
                label_text = " ".join(
                    str(value or "")
                    for value in (
                        getattr(segment, "SegmentLabel", ""),
                        getattr(segment, "SegmentDescription", ""),
                    )
                ).strip().lower()
                mapped_id = LUNG_SEGMENT_LABEL_TO_ID.get(label_text)
                if mapped_id is not None:
                    segment_number_to_id[segment_number] = mapped_id

            if not segment_number_to_id:
                continue

            label_volume = np.zeros(
                (len(ct_slices), int(seg_ds.Rows), int(seg_ds.Columns)),
                dtype=np.uint8,
            )
            frame_groups = getattr(seg_ds, "PerFrameFunctionalGroupsSequence", [])
            for frame_index, frame in enumerate(frame_groups):
                if frame_index >= seg_pixels.shape[0]:
                    break
                try:
                    segment_number = int(
                        frame.SegmentIdentificationSequence[0].ReferencedSegmentNumber
                    )
                    image_position = np.asarray(
                        [float(v) for v in frame.PlanePositionSequence[0].ImagePositionPatient],
                        dtype=np.float64,
                    )
                except Exception:
                    continue

                label_id = segment_number_to_id.get(segment_number)
                if label_id is None:
                    continue

                z_index = int(np.argmin(np.abs(np.asarray(ct_positions) - float(np.dot(image_position, slice_direction)))))
                frame_mask = seg_pixels[frame_index] > 0
                if not np.any(frame_mask):
                    continue

                if label_id == 100:
                    label_volume[z_index][frame_mask] = label_id
                else:
                    target = label_volume[z_index]
                    target[frame_mask & (target == 0)] = label_id

            if need_flip:
                label_volume = label_volume[::-1, :, :].copy()

            if zoom_factor != 1.0:
                label_volume = zoom(
                    label_volume,
                    (zoom_factor, zoom_factor, zoom_factor),
                    order=0,
                    mode="constant",
                    cval=0,
                ).astype(np.uint8)

            if label_volume.shape != output_shape:
                logger.warning(
                    "DICOM SEG label shape %s does not match CT output shape %s",
                    label_volume.shape,
                    output_shape,
                )
                continue

            label_counts = {
                int(label_id): int(np.sum(label_volume == label_id))
                for label_id in LUNG_SAMPLE_LABEL_MAP
                if label_id != 0 and int(np.sum(label_volume == label_id)) > 0
            }
            if not label_counts:
                continue

            slice_presence: Dict[str, list] = {}
            groups = {
                "lung": [13, 14],
                "lung_left": [13],
                "lung_right": [14],
                "spinal_cord": [21],
                "neoplasm": [100],
            }
            for group_name, label_ids in groups.items():
                z_presence = np.any(np.isin(label_volume, label_ids), axis=(1, 2))
                z_indices = np.where(z_presence)[0]
                if len(z_indices) > 0:
                    slice_presence[group_name] = [int(z_indices[0]), int(z_indices[-1])]

            return label_volume, label_counts, slice_presence, seg_series.id

    return None, {}, {}, None


def _label_defs_to_label_map(label_defs: list[dict]) -> Dict[int, str]:
    label_map: Dict[int, str] = {}
    for label_def in label_defs:
        try:
            label_index = int(label_def.get("index", 0))
        except (TypeError, ValueError):
            continue
        if label_index <= 0:
            continue
        raw_name = label_def.get("name") or f"label_{label_index}"
        label_map[label_index] = str(raw_name).strip().lower().replace(" ", "_")
    return label_map


def _summarize_label_volume(
    label_volume: np.ndarray,
    label_map: Dict[int, str],
) -> tuple[Dict[int, int], Dict[str, list]]:
    label_counts: Dict[int, int] = {}
    slice_presence: Dict[str, list] = {}

    for label_id, label_name in label_map.items():
        mask = label_volume == int(label_id)
        count = int(np.sum(mask))
        if count <= 0:
            continue
        label_counts[int(label_id)] = count

        z_presence = np.any(mask, axis=(1, 2))
        z_indices = np.where(z_presence)[0]
        if len(z_indices) > 0:
            slice_presence[str(label_name)] = [int(z_indices[0]), int(z_indices[-1])]

    return label_counts, slice_presence


def _load_nnunet_workspace_label_volume(
    volume: np.ndarray,
    spacing: tuple[float, float, float],
) -> tuple[Optional[np.ndarray], Dict[int, str], Dict[int, int], Dict[str, list], Optional[str], Optional[str]]:
    """Run the locally mounted nnUNet model as a DICOM workspace label fallback."""
    model_attempts = [
        "nnunet702_20organs",
        "nnunet_lung_lobe",
        "nnunet_handoff",
    ]
    last_error: Optional[str] = None

    for model_name in model_attempts:
        try:
            if model_name == "nnunet702_20organs":
                from app.ai.nnunet_custom_20 import (
                    is_available as is_nnunet20_available,
                    run_nnunet_custom_20,
                )
                from app.ai.nnunet_custom_20.labels import get_label_defs

                if not is_nnunet20_available():
                    continue
                label_volume = run_nnunet_custom_20(
                    volume=volume,
                    spacing=spacing,
                    merge_to_6=False,
                )
                label_map = _label_defs_to_label_map(get_label_defs())

            elif model_name == "nnunet_lung_lobe":
                from app.ai.nnunet_lung_lobe import (
                    is_available as is_lung_lobe_available,
                    remap_lung_lobe_labels_to_upper_body,
                    run_nnunet_lung_lobe,
                )
                from app.ai.nnunet_lung_lobe.labels import get_label_defs

                if not is_lung_lobe_available():
                    continue
                raw_label_volume = run_nnunet_lung_lobe(
                    volume=volume,
                    spacing=spacing,
                )
                label_volume = remap_lung_lobe_labels_to_upper_body(raw_label_volume)
                label_map = _label_defs_to_label_map(get_label_defs())

            else:
                from app.ai.nnunet_custom import (
                    is_available as is_nnunet_available,
                    run_nnunet_custom,
                )
                from app.ai.nnunet_custom.labels import get_label_defs

                if not is_nnunet_available():
                    continue
                label_volume = run_nnunet_custom(
                    volume=volume,
                    spacing=spacing,
                )
                label_map = _label_defs_to_label_map(get_label_defs())

            label_volume = np.asarray(label_volume, dtype=np.uint8)
            if label_volume.shape != volume.shape:
                logger.warning(
                    "nnUNet label shape %s does not match CT shape %s for model %s",
                    label_volume.shape,
                    volume.shape,
                    model_name,
                )
                continue

            label_counts, slice_presence = _summarize_label_volume(label_volume, label_map)
            if not label_counts:
                logger.warning("nnUNet model %s returned no foreground labels", model_name)
                continue

            return label_volume, label_map, label_counts, slice_presence, model_name, None

        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:240]}"
            logger.warning("DICOM workspace nnUNet fallback failed for %s: %s", model_name, last_error, exc_info=True)

    return None, {}, {}, {}, None, last_error


def _build_workspace_volume_payload(
    volume: np.ndarray,
    metadata: Dict[str, Any],
    *,
    label_volume: Optional[np.ndarray] = None,
    include_labels: bool = True,
) -> Dict[str, Any]:
    response_metadata = dict(metadata)
    response_metadata["labels_enabled"] = bool(include_labels)
    if not include_labels:
        response_metadata["label_map"] = {}
        response_metadata["label_nonzero_counts"] = {}
        response_metadata["slice_label_presence"] = {}
        response_metadata["label_source"] = None
        response_metadata["label_model_name"] = None
        response_metadata["segmentation_series_id"] = None

    response_content: Dict[str, Any] = {
        "volume_base64": base64.b64encode(
            np.asarray(volume, dtype="<f4").tobytes()
        ).decode("ascii"),
        "label_base64": None,
        "metadata": response_metadata,
    }
    if include_labels and label_volume is not None:
        response_content["label_base64"] = base64.b64encode(
            np.asarray(label_volume, dtype=np.uint8).tobytes()
        ).decode("ascii")
    return response_content


def _build_standardized_ct_case(
    *,
    source: str,
    source_case_id: Optional[str],
    simulated_volume: np.ndarray,
    spacing: tuple[float, float, float],
    params_json: Dict[str, Any],
    metadata: Dict[str, Any],
    origin: Optional[Any] = None,
    direction: Optional[Any] = None,
    body_part: Optional[str] = None,
) -> Dict[str, Any]:
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    hu_range = metadata.get("hu_range") or [
        float(np.min(simulated_volume)),
        float(np.max(simulated_volume)),
    ]
    case_id = (
        f"sim_{source}_{source_case_id}_{timestamp}"
        if source_case_id
        else f"sim_{source}_{timestamp}"
    )
    return {
        "case_id": case_id,
        "source": source,
        "source_case_id": source_case_id,
        "volume": {
            "encoding": "base64",
            "dtype": "float32",
            "byte_order": "little_endian",
            "axis_order": "zyx",
            "shape": [int(v) for v in simulated_volume.shape],
            "spacing": [float(v) for v in spacing],
            "origin": _normalize_origin(origin),
            "direction": _normalize_direction(direction),
            "hu_range": [float(hu_range[0]), float(hu_range[1])],
            "slice_count": int(simulated_volume.shape[0]),
            "modality": "CT",
            "body_part": body_part or "unknown",
            "image_kind": "simulated_ct",
            "image_data_field": "simulated_volume_base64",
            "spatial_reference": metadata.get("spatial_reference", "local_volume_space"),
        },
        "simulation": {
            "type": "ct_scan_params",
            "params_json": params_json,
            "algorithm": "image_domain_approximation",
            "approximation_warning": "This is an educational image-domain approximation, not a scanner-physics simulation.",
        },
        "metadata": metadata,
    }


# ---------------------------------------------------------------------------
# Background task: run simulation job
# ---------------------------------------------------------------------------


def run_simulation_job(job_id: str) -> None:
    """
    Execute a simulation job in the background.

    Creates its own database session (does NOT reuse the request session)
    to avoid cross-thread / cross-request session reuse issues.

    Status flow:
        pending -> running -> completed / failed

    Phase 2 implementation:
      1. Build base volume (from DICOM source or synthetic fallback)
      2. Apply LesionGenerator / OrganSimulator overlays
      3. Write result as NRRD via SimpleITK to a temp file
      4. Upload temp file to storage backend
      5. Set job.output_path = "simulation/{job_id}/result.nrrd"
      6. Clean up temp files

    output_path always holds a storage object_key (never a local path).
    """
    import SimpleITK as sitk
    import numpy as np

    db: Session = SessionLocal()
    temp_nrrd_path: Optional[str] = None

    try:
        job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
        if not job:
            logger.error("run_simulation_job: job %s not found, exiting", job_id)
            return

        # --- Transition: pending -> running ---
        job.status = "running"
        job.progress = 10.0
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info("Job %s transitioned to running", job_id)

        # --- Step 1: Build base volume (progress -> 25) ---
        storage = get_storage_backend()
        volume: Optional[np.ndarray] = None
        metadata: Optional[dict] = None

        if job.series_id:
            # Try to read source DICOM from storage backend
            instances = (
                db.query(DicomInstance)
                .filter(DicomInstance.series_id == job.series_id)
                .order_by(DicomInstance.instance_number.asc().nulls_last())
                .all()
            )
            if instances:
                try:
                    volume, metadata = build_volume_from_dicom(storage, instances)
                    logger.info(
                        "Job %s: built volume from DICOM series %s (%d slices)",
                        job_id, job.series_id, len(instances),
                    )
                except Exception as e:
                    logger.warning(
                        "Job %s: failed to build volume from DICOM (%s), "
                        "falling back to synthetic",
                        job_id, e,
                    )
                    volume, metadata = None, None

        if volume is None:
            # No source DICOM or read failed 鈥?generate synthetic base volume
            volume, metadata = build_synthetic_volume()
            logger.info("Job %s: using synthetic base volume", job_id)

        job.progress = 25.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 2: Apply simulation overlays (progress -> 50) ---
        result_volume = volume.copy()

        # Apply lesions
        lesion_configs = (
            db.query(LesionConfig)
            .filter(LesionConfig.job_id == job_id)
            .all()
        )
        if lesion_configs:
            lesion_gen = LesionGenerator()
            spacing = metadata.get("spacing")
            for lc in lesion_configs:
                _label = f"job_{job_id[:8]}_{lc.lesion_type}_{lc.id[:8]}"

                # Normalize center: if all zeros (frontend default), place at volume center
                cz, cy, cx = lc.center_z, lc.center_y, lc.center_x
                if cz == 0.0 and cy == 0.0 and cx == 0.0:
                    cz = float(result_volume.shape[0] // 2)
                    cy = float(result_volume.shape[1] // 2)
                    cx = float(result_volume.shape[2] // 2)
                config_dict = {
                    "lesion_type": lc.lesion_type,
                    "shape": lc.shape,
                    "center_x": cx,
                    "center_y": cy,
                    "center_z": cz,
                    "radius_x": lc.radius_x,
                    "radius_y": lc.radius_y,
                    "radius_z": lc.radius_z,
                    "hu_mean": lc.hu_mean,
                    "hu_std": lc.hu_std,
                    "margin_sharpness": lc.margin_sharpness,
                    "calcification_fraction": lc.calcification_fraction,
                    "necrosis_fraction": lc.necrosis_fraction,
                    "spiculation_degree": lc.spiculation_degree,
                    # P0: Mesh / mask template support
                    "mesh_path": lc.mesh_path,
                    "mask_path": lc.mask_path,
                    # P1: Texture generation
                    "texture_config": lc.texture_config,
                    # P2: Organ-aware placement
                    "organ_constraint": lc.organ_constraint,
                }
                # ── DEBUG: spacing verification (Task 4) ──
                _debug_log_spacing(
                    spacing=spacing,
                    radius_mm=(lc.radius_z, lc.radius_y, lc.radius_x),
                    label=_label,
                )

                lesion_vol = lesion_gen.generate_lesion(
                    volume_shape=result_volume.shape,
                    config=config_dict,
                    spacing=spacing,
                    mesh_path=lc.mesh_path,
                    mask_path=lc.mask_path,
                )
                lesion_mask = lesion_vol != 0

                # ── DEBUG: position validation (Task 3) ──
                _debug_log_position(
                    lesion_mask=lesion_mask,
                    center=(cz, cy, cx),
                    volume_shape=result_volume.shape,
                    label=_label,
                )

                # ── DEBUG: before/after write stats (Task 2) ──
                _base_before = result_volume.copy()
                result_volume[lesion_mask] = lesion_vol[lesion_mask]
                _debug_log_lesion_write(
                    base_volume=_base_before,
                    result_volume=result_volume,
                    label=_label,
                )

                # ── DEBUG: save visualization PNGs (Task 5) ──
                _debug_save_lesion_pngs(
                    base_volume=volume,
                    result_volume=result_volume,
                    lesion_volume=lesion_vol,
                    lesion_mask=lesion_mask,
                    label=_label,
                )

                logger.info(
                    "Job %s: applied lesion %s (%s) — voxels=%d",
                    job_id, lc.id, lc.lesion_type, int(lesion_mask.sum()),
                )

        # Apply organs
        organ_configs = (
            db.query(OrganConfig)
            .filter(OrganConfig.job_id == job_id)
            .all()
        )
        if organ_configs:
            organ_sim = OrganSimulator()
            for oc in organ_configs:
                config_dict = {
                    "organ_type": oc.organ_type,
                    "hu_mean": oc.hu_mean,
                    "hu_std": oc.hu_std,
                    "enable_noise": oc.enable_noise,
                    "noise_level": oc.noise_level,
                    "enable_enhancement": oc.enable_enhancement,
                    "enhancement_pattern": oc.enhancement_pattern,
                }
                organ_vol = organ_sim.generate_organ(
                    volume_shape=result_volume.shape,
                    config=config_dict,
                )
                # Only add organ where result is still background (avoid overwriting lesions)
                organ_mask = organ_vol != 0
                result_volume[organ_mask] = organ_vol[organ_mask]
                logger.info(
                    "Job %s: applied organ %s (%s)",
                    job_id, oc.id, oc.organ_type,
                )

        job.progress = 50.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 3: Write NRRD to temp file (progress -> 75) ---
        spacing = metadata.get("spacing", (1.0, 1.0, 1.0))
        origin = metadata.get("origin", (0.0, 0.0, 0.0))
        direction = metadata.get("direction", (1, 0, 0, 0, 1, 0, 0, 0, 1))

        # SimpleITK expects spacing/origin in (x, y, z) order, but our
        # volume is (z, y, x). Reverse for SimpleITK compatibility.
        sitk_spacing = (spacing[2], spacing[1], spacing[0])
        sitk_origin = (origin[2], origin[1], origin[0])

        sitk_image = sitk.GetImageFromArray(result_volume.astype(np.float32))
        sitk_image.SetSpacing(sitk_spacing)
        sitk_image.SetOrigin(sitk_origin)
        if len(direction) == 9:
            sitk_image.SetDirection(direction)

        # Write to temp file
        temp_dir = tempfile.mkdtemp(prefix=f"sim_{job_id}_")
        temp_nrrd_path = os.path.join(temp_dir, "result.nrrd")
        sitk.WriteImage(sitk_image, temp_nrrd_path)
        logger.info(
            "Job %s: wrote temp NRRD %s (%.1f MB)",
            job_id, temp_nrrd_path,
            os.path.getsize(temp_nrrd_path) / (1024 * 1024),
        )

        # ── DEBUG: verify SimpleITK metadata round-trip (Task 6) ──
        _debug_verify_sitk_metadata(
            sitk_image=sitk_image,
            nrrd_path=temp_nrrd_path,
            label=f"job_{job_id[:8]}",
        )

        job.progress = 75.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 4: Upload to storage backend (progress -> 90) ---
        object_key = f"simulation/{job_id}/result.nrrd"
        upload_ok = storage.upload_file(
            object_key=object_key,
            file_path=temp_nrrd_path,
            content_type="application/octet-stream",
        )
        if not upload_ok:
            raise RuntimeError(
                f"Failed to upload result NRRD to storage (key={object_key})"
            )
        logger.info("Job %s: uploaded result to storage key=%s", job_id, object_key)

        job.progress = 90.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 5: Mark completed with output_path (progress -> 100) ---
        job.output_path = object_key
        job.output_format = "nrrd"
        job.status = "completed"
        job.progress = 100.0
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info("Job %s completed: output_path=%s", job_id, object_key)

    except Exception as e:
        logger.exception("run_simulation_job: unhandled error for job %s", job_id)
        try:
            db.rollback()
            job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = f"{type(e).__name__}: {str(e)[:200]}"
                job.updated_at = datetime.utcnow()
                # output_path stays None on failure 鈥?never write a fake value
                db.commit()
                logger.info("Job %s marked as failed", job_id)
        except Exception:
            logger.exception(
                "run_simulation_job: failed to update job %s to failed state",
                job_id,
            )
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        # Clean up temp files
        if temp_nrrd_path:
            try:
                temp_dir = os.path.dirname(temp_nrrd_path)
                if os.path.isdir(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info("Job %s: cleaned up temp dir %s", job_id, temp_dir)
            except Exception:
                logger.warning("Job %s: failed to clean temp dir", job_id)

        db.close()


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/jobs", response_model=SimulationJobResponse, status_code=status.HTTP_201_CREATED)
async def create_simulation_job(
    config: SimulationJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a new simulation job and enqueue it for background execution.

    The job starts with status='pending'. After the HTTP response is sent,
    FastAPI triggers run_simulation_job() in the background, which
    transitions the job through running -> completed/failed.
    """
    job_id = str(uuid.uuid4())

    job = SimulationJob(
        id=job_id,
        study_id=config.study_id,
        series_id=config.series_id,
        status="pending",
        lesion_count=len(config.lesions),
        organ_count=len(config.organs),
        output_format=config.output_format,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(job)

    # Create lesion configurations
    for i, lesion_config in enumerate(config.lesions):
        lesion = LesionConfig(
            id=str(uuid.uuid4()),
            job_id=job_id,
            lesion_type=lesion_config.lesion_type,
            shape=lesion_config.shape,
            center_x=lesion_config.center_x,
            center_y=lesion_config.center_y,
            center_z=lesion_config.center_z,
            radius_x=lesion_config.radius_x,
            radius_y=lesion_config.radius_y,
            radius_z=lesion_config.radius_z,
            hu_mean=lesion_config.hu_mean,
            hu_std=lesion_config.hu_std,
            margin_sharpness=lesion_config.margin_sharpness,
            calcification_fraction=lesion_config.calcification_fraction,
            necrosis_fraction=lesion_config.necrosis_fraction,
            spiculation_degree=lesion_config.spiculation_degree,
        )
        db.add(lesion)

    db.commit()
    db.refresh(job)

    # Enqueue background execution 鈥?job_id is a plain string,
    # the background task creates its own DB session.
    background_tasks.add_task(run_simulation_job, job_id)

    return job


@router.get("/jobs", response_model=List[SimulationJobResponse])
async def list_simulation_jobs(
    study_id: Optional[str] = Query(None, description="Filter by study"),
    status_filter: Optional[str] = Query(None, description="Filter by status"),
    db: Session = Depends(get_db),
):
    """List simulation jobs with optional filtering."""
    query = db.query(SimulationJob)

    if study_id:
        query = query.filter(SimulationJob.study_id == study_id)
    if status_filter:
        query = query.filter(SimulationJob.status == status_filter)

    jobs = query.order_by(SimulationJob.created_at.desc()).all()
    return jobs


@router.get("/jobs/{job_id}", response_model=SimulationJobResponse)
async def get_simulation_job(job_id: str, db: Session = Depends(get_db)):
    """Get the status and details of a simulation job."""
    job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation job {job_id} not found",
        )
    return job


@router.post("/jobs/{job_id}/cancel")
async def cancel_simulation_job(job_id: str, db: Session = Depends(get_db)):
    """Cancel a running simulation job."""
    job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation job {job_id} not found",
        )
    if job.status not in ("pending", "running"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot cancel job in status '{job.status}'",
        )
    job.status = "failed"
    job.error_message = "Cancelled by user"
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled", "job_id": job_id}


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


@router.post("/preview/lesion", response_model=SimulationPreviewResponse)
async def preview_lesion(config: dict):
    """
    Generate a fast preview of a lesion configuration.

    Synchronous endpoint for real-time preview of lesion parameters
    without creating a full simulation job.
    """
    try:
        generator = LesionGenerator()
        preview = generator.generate_preview(config)
        return SimulationPreviewResponse(
            job_id=str(uuid.uuid4()),
            preview_data=preview,
            voxel_count=preview.get("voxel_count", 0),
            hu_range=(preview.get("hu_min", 0), preview.get("hu_max", 0)),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preview generation failed: {str(e)}",
        )


@router.post("/preview/lesion-3d", response_model=Lesion3DPreviewResponse)
async def preview_lesion_3d(request: Lesion3DPreviewRequest):
    """
    Generate a 3D triangle mesh preview of a lesion configuration.

    Returns vertices, faces, and normals suitable for direct rendering
    in vtk.js via vtkPolyData + vtkActor.

    Supports all 5 shape types (spherical, ellipsoidal, lobulated,
    spiculated, irregular) as well as MeshGenerator and MaskGenerator modes.

    The preview volume is auto-sized based on lesion radii to balance
    mesh quality and performance.
    """
    try:
        # ── 1. Resolve spacing ──
        spacing = tuple(request.spacing or [1.0, 1.0, 1.0])
        if len(spacing) != 3:
            spacing = (1.0, 1.0, 1.0)

        # ── 2. Auto-size preview volume ──
        # Ensure the volume is large enough to contain the lesion + margin
        max_radius_mm = max(
            abs(request.radius_x or 10),
            abs(request.radius_y or 10),
            abs(request.radius_z or 10),
        )
        min_spacing = min(spacing)
        edge_voxels = max(
            request.preview_size,
            int(2.0 * max_radius_mm / min_spacing * 1.6 + 12),  # +margin
        )
        # Keep within bounds
        edge_voxels = min(edge_voxels, 256)
        volume_shape = (edge_voxels, edge_voxels, edge_voxels)

        # ── 3. Normalise center (0 → auto-center) ──
        cz, cy, cx = request.center_z, request.center_y, request.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume_shape[0] // 2)
            cy = float(volume_shape[1] // 2)
            cx = float(volume_shape[2] // 2)

        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }

        # ── 4. Generate lesion volume ──
        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=volume_shape,
            config=config_dict,
            spacing=spacing,
            mesh_path=request.mesh_path,
            mask_path=request.mask_path,
        )

        # ── 5. Extract binary mask ──
        lesion_mask = lesion_vol != 0

        if not lesion_mask.any():
            return Lesion3DPreviewResponse(
                vertices=[],
                faces=[],
                normals=[],
                bounds={"min": [0, 0, 0], "max": [0, 0, 0]},
                center=[0, 0, 0],
                volume_mm3=0.0,
            )

        # ── 6. Extract 3D mesh via Marching Cubes ──
        mesh_data = extract_mesh(lesion_mask, spacing=spacing)

        return Lesion3DPreviewResponse(
            vertices=mesh_data["vertices"],
            faces=mesh_data["faces"],
            normals=mesh_data["normals"],
            bounds=mesh_data["bounds"],
            center=mesh_data["center"],
            volume_mm3=mesh_data["volume_mm3"],
        )

    except Exception as e:
        logger.exception("3D lesion preview endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"3D lesion preview failed: {str(e)[:500]}",
        )


@router.post("/preview/lesion-on-dicom", response_model=DicomLesionPreviewResponse)
async def preview_lesion_on_dicom(
    request: DicomLesionPreviewRequest,
    db: Session = Depends(get_db),
):
    """
    Preview a lesion overlaid on a real DICOM series.

    Loads the DICOM series from storage, generates the lesion on it,
    and returns a base64-encoded side-by-side PNG (original | with lesion)
    of the axial slice through the lesion center.

    Synchronous 鈥?intended for interactive parameter tuning.
    """
    try:
        # 鈹€鈹€ 1. Load DICOM instances 鈹€鈹€
        instances = (
            db.query(DicomInstance)
            .filter(DicomInstance.series_id == request.series_id)
            .order_by(DicomInstance.instance_number.asc().nulls_last())
            .all()
        )
        if not instances:
            raise HTTPException(status_code=404, detail=f"Series {request.series_id} not found")

        storage = get_storage_backend()
        volume, metadata = build_volume_from_dicom(storage, instances)
        volume, metadata = _normalize_dicom_scan_direction(
            volume,
            metadata,
            request.scan_direction,
        )

        # 鈹€鈹€ 2. Generate lesion 鈹€鈹€
        generator = LesionGenerator()
        lesion_cfg = request.lesion

        # Normalize center: if all zeros (frontend default), place at volume center
        cz, cy, cx = lesion_cfg.center_z, lesion_cfg.center_y, lesion_cfg.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume.shape[0] // 2)
            cy = float(volume.shape[1] // 2)
            cx = float(volume.shape[2] // 2)

        config_dict = {
            "lesion_type": lesion_cfg.lesion_type,
            "shape": lesion_cfg.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": lesion_cfg.radius_x,
            "radius_y": lesion_cfg.radius_y,
            "radius_z": lesion_cfg.radius_z,
            "hu_mean": lesion_cfg.hu_mean,
            "hu_std": lesion_cfg.hu_std,
            "margin_sharpness": lesion_cfg.margin_sharpness,
            "calcification_fraction": lesion_cfg.calcification_fraction,
            "necrosis_fraction": lesion_cfg.necrosis_fraction,
            "spiculation_degree": lesion_cfg.spiculation_degree,
            # P0: Mesh / mask template support
            "mesh_path": lesion_cfg.mesh_path,
            "mask_path": lesion_cfg.mask_path,
            # P1: Texture generation
            "texture_config": lesion_cfg.texture_config,
            # P2: Organ-aware placement
            "organ_constraint": lesion_cfg.organ_constraint,
        }
        spacing = metadata.get("spacing")
        lesion_vol = generator.generate_lesion(
            volume_shape=volume.shape,
            config=config_dict,
            spacing=spacing,
            mesh_path=lesion_cfg.mesh_path,
            mask_path=lesion_cfg.mask_path,
        )
        lesion_mask = lesion_vol != 0
        result_volume = volume.copy()
        result_volume[lesion_mask] = lesion_vol[lesion_mask]

        # Compute lesion region stats.
        if lesion_mask.any():
            hu_values = result_volume[lesion_mask]
            stats_voxels = int(np.sum(lesion_mask))
            stats_hu_min = float(np.min(hu_values))
            stats_hu_max = float(np.max(hu_values))
            stats_hu_mean = float(np.mean(hu_values))
            stats_hu_std = float(np.std(hu_values))
            spacing = metadata.get("spacing", (1.0, 1.0, 1.0))
            voxel_vol_mm3 = spacing[0] * spacing[1] * spacing[2]
            stats_volume_mm3 = float(stats_voxels * voxel_vol_mm3)
        else:
            stats_voxels = 0
            stats_hu_min = stats_hu_max = stats_hu_mean = stats_hu_std = 0.0
            stats_volume_mm3 = 0.0

        # 鈹€鈹€ 4. Find the slice closest to the lesion center 鈹€鈹€
        cz_idx = int(round(cz))
        cz_idx = max(0, min(cz_idx, volume.shape[0] - 1))

        # 鈹€鈹€ 5. Render side-by-side preview PNG 鈹€鈹€
        wc = request.window_center
        ww = request.window_width
        half = ww / 2.0
        lower = wc - half
        upper = wc + half

        def slice_to_8bit(slice_2d: np.ndarray) -> np.ndarray:
            return np.clip((slice_2d - lower) / ww * 255, 0, 255).astype(np.uint8)

        before_slice = slice_to_8bit(volume[cz_idx, :, :])
        after_slice = slice_to_8bit(result_volume[cz_idx, :, :])

        # Side-by-side
        h, w = before_slice.shape
        combined = np.zeros((h, w * 2 + 4), dtype=np.uint8)
        combined[:, :w] = before_slice
        # Divider (light gray line)
        combined[:, w:w + 4] = 160
        combined[:, w + 4:] = after_slice

        # Add labels (original / with lesion) 鈥?simple pixel-based label
        # "Original" label at top-left
        combined[4:10, 6:6 + 8 * 6] = 200  # placeholder bar
        # "With Lesion" label at top-right
        combined[4:10, w + 10:w + 10 + 11 * 6] = 200

        img = Image.fromarray(combined, mode="L")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return DicomLesionPreviewResponse(
            image_base64=b64,
            slice_index=cz_idx,
            total_slices=volume.shape[0],
            lesion_center_voxel=[cz_idx, int(round(cy)), int(round(cx))],
            hu_min=stats_hu_min,
            hu_max=stats_hu_max,
            hu_mean=stats_hu_mean,
            hu_std=stats_hu_std,
            voxel_count=stats_voxels,
            volume_mm3=stats_volume_mm3,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DICOM lesion preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DICOM lesion preview failed: {str(e)[:300]}",
        )


# ---------------------------------------------------------------------------
# Lesion-in-Phantom 3D Preview — CT phantom body + embedded lesion
# ---------------------------------------------------------------------------


@router.post(
    "/preview/lesion-in-phantom",
    response_model=LesionInPhantomPreviewResponse,
)
async def preview_lesion_in_phantom(request: LesionInPhantomPreviewRequest):
    """
    Generate a lesion embedded inside a CT phantom body for 3D preview.

    Step-by-step:
      1. Generate a procedural upper-body CT phantom (lungs, bones, organs).
      2. Auto-place the lesion in the right lung if center not specified.
      3. Generate the lesion voxels and bake them into the phantom volume.
      4. Extract the lesion triangle mesh via Marching Cubes.
      5. Offset mesh vertices so they align with VTK's centered volume origin.

    Returns both the phantom volume (base64) and the lesion mesh geometry,
    ready for frontend VolumeRenderer in mode='synthetic' with lesionMeshes.
    """
    try:
        # ── 1. Generate CT phantom ──
        volume, _, metadata = generate_procedural_ct_phantom(
            size=request.phantom_size,
        )
        shape = volume.shape  # (z, y, x)
        spacing = tuple(metadata["spacing"])  # (sz, sy, sx)

        # ── 2. Determine lesion center in phantom voxel space ──
        # Priority: normalized_center (0-1) > raw center > auto-place
        ncx, ncy, ncz = (
            request.normalized_center_x,
            request.normalized_center_y,
            request.normalized_center_z,
        )
        if ncx > 0 and ncy > 0 and ncz > 0:
            # Scale normalized coords to this phantom's dimensions
            cz = ncz * float(shape[0])
            cy = ncy * float(shape[1])
            cx = ncx * float(shape[2])
            logger.info(
                "Lesion position from normalized coords: (%.3f, %.3f, %.33) → voxel (%.1f, %.1f, %.1f)",
                ncx, ncy, ncz, cx, cy, cz,
            )
        else:
            cz, cy, cx = request.center_z, request.center_y, request.center_x
            if cz == 0.0 and cy == 0.0 and cx == 0.0:
                # Auto-place in the right lung region
                # Phantom normalized coords: lungs in upper ~60% (zn < 0.2)
                # Right lung center ≈ (0.35, 0.0) normalized → voxel
                cz = float(shape[0]) * 0.33   # ~1/3 from top (upper chest)
                cy = float(shape[1]) * 0.50   # mid anterior-posterior
                cx = float(shape[2]) * 0.65   # ~2/3 from left → right lung

        # ── 3. Generate lesion in phantom space ──
        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }
        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=shape,
            config=config_dict,
            spacing=spacing,
        )

        # ── 4. Bake lesion into phantom ──
        lesion_mask = lesion_vol != 0
        result_volume = volume.copy()
        result_volume[lesion_mask] = lesion_vol[lesion_mask]

        logger.info(
            "Lesion-in-phantom: size=%s lesion_voxels=%d center=(%.1f, %.1f, %.1f)",
            list(shape), int(lesion_mask.sum()), cz, cy, cx,
        )

        # ── 5. Extract lesion mesh ──
        mesh_data = extract_mesh(lesion_mask, spacing=spacing)

        # ── 6. Offset mesh vertices to match VTK centered volume origin ──
        # VolumeRenderer.centerVolumeOrigin([x, y, z], [sx, sy, sz]) returns:
        #   ox = -((x-1) * sx) / 2
        #   oy = -((y-1) * sy) / 2
        #   oz = -((z-1) * sz) / 2
        # mesh vertices are in physical mm where voxel 0 → (0,0,0)
        # so we add the centered origin to each vertex.
        sx, sy, sz = spacing[2], spacing[1], spacing[0]  # (z,y,x) → (x,y,z)
        dx, dy, dz = shape[2], shape[1], shape[0]
        origin_x = -((dx - 1) * sx) / 2.0
        origin_y = -((dy - 1) * sy) / 2.0
        origin_z = -((dz - 1) * sz) / 2.0

        raw_verts = mesh_data["vertices"]  # list of [x, y, z] in local mm
        centered_verts = [
            [v[0] + origin_x, v[1] + origin_y, v[2] + origin_z]
            for v in raw_verts
        ]

        centered_center = mesh_data["center"].copy()
        centered_center[0] += origin_x
        centered_center[1] += origin_y
        centered_center[2] += origin_z

        # ── 7. Encode volume ──
        volume_b64 = base64.b64encode(
            np.asarray(result_volume, dtype="<f4").tobytes()
        ).decode("ascii")

        return LesionInPhantomPreviewResponse(
            phantom_volume_base64=volume_b64,
            phantom_shape=list(shape),
            phantom_spacing=list(spacing),
            lesion_vertices=centered_verts,
            lesion_faces=mesh_data["faces"],
            lesion_normals=mesh_data["normals"],
            lesion_center_mm=centered_center,
            lesion_volume_mm3=mesh_data["volume_mm3"],
        )

    except Exception as e:
        logger.exception("Lesion-in-phantom preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lesion-in-phantom preview failed: {str(e)[:500]}",
        )


# ---------------------------------------------------------------------------
# DICOM 3D Lesion Preview — lesion embedded in real DICOM volume
# ---------------------------------------------------------------------------


@router.post(
    "/preview/lesion-on-dicom-3d",
    response_model=DicomLesion3DPreviewResponse,
)
async def preview_lesion_on_dicom_3d(
    request: DicomLesion3DPreviewRequest,
    db: Session = Depends(get_db),
):
    """
    Generate a 3D preview of a lesion embedded inside a real DICOM volume.

    Step-by-step:
      1. Load the DICOM series and build a 3D volume.
      2. Downsample the volume to ``preview_size`` (manageable for base64 transfer).
      3. Map the normalized center coords to the downsampled voxel space.
      4. Generate the lesion at that position (mm radii, HU values).
      5. Bake the lesion into the downsampled volume.
      6. Extract the lesion triangle mesh via Marching Cubes.
      7. Offset mesh vertices to align with VTK's centered volume origin.

    Returns the downsampled volume (base64) + lesion mesh geometry,
    ready for frontend VolumeRenderer in mode='synthetic' with lesionMeshes.
    """
    try:
        # ── 1. Load DICOM instances ──
        instances = (
            db.query(DicomInstance)
            .filter(DicomInstance.series_id == request.series_id)
            .order_by(DicomInstance.instance_number.asc().nulls_last())
            .all()
        )
        if not instances:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Series {request.series_id} not found",
            )

        storage = get_storage_backend()
        volume, metadata = build_volume_from_dicom(storage, instances)
        volume, metadata = _normalize_dicom_scan_direction(
            volume,
            metadata,
            request.scan_direction,
        )
        original_shape = volume.shape  # (z, y, x)
        original_spacing = tuple(metadata.get("spacing", (1.0, 1.0, 1.0)))

        logger.info(
            "DICOM 3D preview: original shape=%s spacing=%s",
            list(original_shape), list(original_spacing),
        )

        # ── 2. Downsample to preview_size if needed ──
        max_dim = max(original_shape)
        if max_dim > request.preview_size:
            scale = request.preview_size / max_dim
            new_shape = tuple(
                max(1, int(round(d * scale))) for d in original_shape
            )
            from skimage.transform import resize
            volume_resized = resize(
                volume.astype(np.float32),
                new_shape,
                order=1,          # linear interpolation
                preserve_range=True,
                anti_aliasing=True,
            )
            new_spacing = (
                original_spacing[0] * original_shape[0] / new_shape[0],
                original_spacing[1] * original_shape[1] / new_shape[1],
                original_spacing[2] * original_shape[2] / new_shape[2],
            )
            logger.info(
                "DICOM 3D preview: downsampled %s → %s, spacing %s → %s",
                list(original_shape), list(new_shape),
                [f"{s:.3f}" for s in original_spacing],
                [f"{s:.3f}" for s in new_spacing],
            )
            shape = new_shape
            spacing = new_spacing
            result_volume = volume_resized.astype(np.float32)
        else:
            shape = original_shape
            spacing = original_spacing
            result_volume = volume.copy()

        # ── 3. Determine lesion center in the (downsampled) volume ──
        ncx, ncy, ncz = (
            request.normalized_center_x,
            request.normalized_center_y,
            request.normalized_center_z,
        )
        if ncx > 0 and ncy > 0 and ncz > 0:
            cx = ncx * float(shape[2])   # x = width
            cy = ncy * float(shape[1])   # y = height
            cz = ncz * float(shape[0])   # z = depth
            logger.info(
                "DICOM 3D: normalized (%.3f, %.3f, %.3f) → voxel (%.1f, %.1f, %.1f)  shape=%s",
                ncx, ncy, ncz, cx, cy, cz, list(shape),
            )
        else:
            cz = float(shape[0]) * 0.4
            cy = float(shape[1]) * 0.5
            cx = float(shape[2]) * 0.5

        # ── 4. Generate lesion ──
        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }
        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=shape,
            config=config_dict,
            spacing=spacing,
        )
        lesion_mask = lesion_vol != 0

        # ── 5. Bake lesion into volume ──
        result_volume[lesion_mask] = lesion_vol[lesion_mask]
        logger.info(
            "DICOM 3D preview: lesion generated — voxels=%d center=(%.1f, %.1f, %.1f)",
            int(lesion_mask.sum()), cz, cy, cx,
        )

        # ── 6. Extract lesion mesh ──
        mesh_data = extract_mesh(lesion_mask, spacing=spacing)

        # ── 7. Offset mesh vertices to VTK centered volume origin ──
        sx, sy, sz = spacing[2], spacing[1], spacing[0]  # (z,y,x) → (x,y,z)
        dx, dy, dz = shape[2], shape[1], shape[0]
        origin_x = -((dx - 1) * sx) / 2.0
        origin_y = -((dy - 1) * sy) / 2.0
        origin_z = -((dz - 1) * sz) / 2.0

        raw_verts = mesh_data["vertices"]
        centered_verts = [
            [v[0] + origin_x, v[1] + origin_y, v[2] + origin_z]
            for v in raw_verts
        ]
        centered_center = mesh_data["center"].copy()
        centered_center[0] += origin_x
        centered_center[1] += origin_y
        centered_center[2] += origin_z

        # ── 8. Encode volume ──
        volume_b64 = base64.b64encode(
            np.asarray(result_volume, dtype="<f4").tobytes()
        ).decode("ascii")

        return DicomLesion3DPreviewResponse(
            volume_base64=volume_b64,
            volume_shape=list(shape),
            volume_spacing=list(spacing),
            lesion_vertices=centered_verts,
            lesion_faces=mesh_data["faces"],
            lesion_normals=mesh_data["normals"],
            lesion_center_mm=centered_center,
            lesion_volume_mm3=mesh_data["volume_mm3"],
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DICOM 3D lesion preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DICOM 3D lesion preview failed: {str(e)[:500]}",
        )


@router.post(
    "/pathology-nodule-on-dicom",
    response_model=PathologyNoduleOnDicomResponse,
)
async def pathology_nodule_on_dicom(
    request: PathologyNoduleOnDicomRequest,
    db: Session = Depends(get_db),
):
    """Generate a pathology-aware lung nodule inside a target segmented lobe."""
    try:
        response_cache_key = (
            str(request.series_id),
            str(request.scan_direction),
            int(request.preview_size),
            str(request.nodule_type),
            str(request.size_category),
            str(request.risk_level),
            str(request.target_lobe),
            request.random_seed,
        )
        cached_response = _get_cached_pathology_preview_response(response_cache_key)
        if cached_response is not None:
            logger.info(
                "Pathology preview response cache hit: series=%s preview_size=%d target_lobe=%s",
                request.series_id,
                request.preview_size,
                request.target_lobe,
            )
            return PathologyNoduleOnDicomResponse(**cached_response)

        instances = (
            db.query(DicomInstance)
            .filter(DicomInstance.series_id == request.series_id)
            .order_by(DicomInstance.instance_number.asc().nulls_last())
            .all()
        )
        if not instances:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Series {request.series_id} not found",
            )

        storage = get_storage_backend()
        volume_cache_key = (
            str(request.series_id),
            str(request.scan_direction),
        )
        cached_volume = _get_cached_pathology_volume(volume_cache_key)
        if cached_volume is not None:
            full_volume, metadata = cached_volume
            logger.info(
                "Pathology volume cache hit: series=%s scan_direction=%s",
                request.series_id,
                request.scan_direction,
            )
        else:
            full_volume, metadata = build_volume_from_dicom(storage, instances)
            full_volume, metadata = _normalize_dicom_scan_direction(
                full_volume,
                metadata,
                request.scan_direction,
            )
            _set_cached_pathology_volume(volume_cache_key, full_volume, metadata)
            logger.info(
                "Pathology volume cache store: series=%s scan_direction=%s shape=%s",
                request.series_id,
                request.scan_direction,
                list(full_volume.shape),
            )
        full_spacing = tuple(float(v) for v in metadata.get("spacing", (1.0, 1.0, 1.0)))

        preview_volume, preview_spacing, preview_scale = _downsample_volume_to_max_dim(
            full_volume,
            full_spacing,
            request.preview_size,
            order=1,
        )

        logger.info(
            "Pathology nodule preview started: series=%s full_shape=%s preview_shape=%s preview_size=%d",
            request.series_id,
            list(full_volume.shape),
            list(preview_volume.shape),
            request.preview_size,
        )

        cache_key = (
            str(request.series_id),
            str(request.scan_direction),
            tuple(int(dim) for dim in preview_volume.shape),
        )
        segmentation_source_model = LUNG_LOBE_MODEL_NAME
        lobe_labels = _get_cached_pathology_lobe_labels(cache_key)
        if lobe_labels is not None:
            logger.info(
                "Pathology lobe cache hit: series=%s preview_shape=%s",
                request.series_id,
                list(preview_volume.shape),
            )
        else:
            (crop_z, crop_y, crop_x) = _estimate_thoracic_crop_bounds(full_volume)
            segmentation_volume = full_volume[crop_z[0]:crop_z[1], crop_y[0]:crop_y[1], crop_x[0]:crop_x[1]]
            segmentation_spacing = full_spacing
            logger.info(
                "Pathology model segmentation crop: series=%s crop_shape=%s crop_bounds_zyx=%s",
                request.series_id,
                list(segmentation_volume.shape),
                {
                    "z": [crop_z[0], crop_z[1]],
                    "y": [crop_y[0], crop_y[1]],
                    "x": [crop_x[0], crop_x[1]],
                },
            )
            try:
                raw_lobe_labels = run_nnunet_lung_lobe(
                    volume=segmentation_volume,
                    spacing=segmentation_spacing,
                )
            except LungLobeModelNotAvailableError as exc:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail=str(exc),
                ) from exc

            cropped_lobe_labels = remap_lung_lobe_labels_to_upper_body(raw_lobe_labels)
            full_lobe_labels = np.zeros(full_volume.shape, dtype=np.uint8)
            full_lobe_labels[
                crop_z[0]:crop_z[1],
                crop_y[0]:crop_y[1],
                crop_x[0]:crop_x[1],
            ] = np.asarray(cropped_lobe_labels, dtype=np.uint8)
            lobe_labels = _resample_label_volume_to_shape(
                full_lobe_labels,
                tuple(int(dim) for dim in preview_volume.shape),
            )
            lobe_labels = np.asarray(lobe_labels, dtype=np.uint8)
            _set_cached_pathology_lobe_labels(cache_key, lobe_labels)
            logger.info(
                "Pathology lobe cache store: series=%s preview_shape=%s source=%s",
                request.series_id,
                list(preview_volume.shape),
                segmentation_source_model,
            )

        target_label = LUNG_LOBE_LABEL_MAP[request.target_lobe]
        target_mask = lobe_labels == int(target_label)
        if not np.any(target_mask):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Target lobe '{request.target_lobe}' was not present in the segmentation result",
            )

        pathology_generator = PathologyGenerator(seed=request.random_seed)
        sampled = pathology_generator.sample(
            nodule_type=request.nodule_type,
            size_category=request.size_category,
            risk_level=request.risk_level,
            target_lobe=request.target_lobe,
        )

        region_determiner = LungRegionDeterminer(seed=request.random_seed)
        placement = region_determiner.find_safe_center(
            ct_volume=preview_volume,
            lobe_mask=target_mask,
            spacing=tuple(float(v) for v in preview_spacing),
            diameter_mm=float(sampled.diameter_mm),
        )

        generator = LesionGenerator(seed=request.random_seed)
        lesion_cfg = sampled.to_generator_config(
            center_x=placement.center_x,
            center_y=placement.center_y,
            center_z=placement.center_z,
        )
        lesion_volume = generator.generate_lesion(
            volume_shape=tuple(int(dim) for dim in preview_volume.shape),
            config=lesion_cfg,
            spacing=tuple(float(v) for v in preview_spacing),
        )
        lesion_mask = lesion_volume != 0
        if not np.any(lesion_mask):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Lesion generator produced an empty mask",
            )

        result_volume = np.asarray(preview_volume, dtype=np.float32).copy()
        result_volume[lesion_mask] = lesion_volume[lesion_mask]
        mesh_data = extract_mesh(lesion_mask, spacing=tuple(float(v) for v in preview_spacing))

        sx, sy, sz = preview_spacing[2], preview_spacing[1], preview_spacing[0]
        dx, dy, dz = result_volume.shape[2], result_volume.shape[1], result_volume.shape[0]
        origin_x = -((dx - 1) * sx) / 2.0
        origin_y = -((dy - 1) * sy) / 2.0
        origin_z = -((dz - 1) * sz) / 2.0
        centered_verts = [
            [v[0] + origin_x, v[1] + origin_y, v[2] + origin_z]
            for v in mesh_data["vertices"]
        ]
        centered_center = list(mesh_data["center"])
        centered_center[0] += origin_x
        centered_center[1] += origin_y
        centered_center[2] += origin_z

        volume_base64 = base64.b64encode(
            np.asarray(result_volume, dtype="<f4").tobytes()
        ).decode("ascii")
        segmentation_mask_base64 = base64.b64encode(
            np.asarray(lobe_labels, dtype="<f4").tobytes()
        ).decode("ascii")

        logger.info(
            "Pathology nodule preview: series=%s preview_shape=%s preview_spacing=%s target_lobe=%s center=%s diameter_mm=%.2f scale=%.3f",
            request.series_id,
            list(result_volume.shape),
            [round(float(v), 3) for v in preview_spacing],
            request.target_lobe,
            [placement.center_z, placement.center_y, placement.center_x],
            sampled.diameter_mm,
            preview_scale,
        )

        response_payload = {
            "volume_base64": volume_base64,
            "volume_shape": [int(dim) for dim in result_volume.shape],
            "volume_spacing": [float(v) for v in preview_spacing],
            "segmentation_mask_base64": segmentation_mask_base64,
            "segmentation_labels": [
                PathologySegmentationLabel(**label_def).model_dump()
                for label_def in get_lung_lobe_label_defs()
            ],
            "segmentation_source_model": segmentation_source_model,
            "lesion_vertices": centered_verts,
            "lesion_faces": mesh_data["faces"],
            "lesion_normals": mesh_data["normals"],
            "lesion_center_mm": [float(v) for v in centered_center],
            "lesion_volume_mm3": float(mesh_data["volume_mm3"]),
            "lesion_center_voxel_zyx": [
                float(placement.center_z),
                float(placement.center_y),
                float(placement.center_x),
            ],
            "sampled_parameters": PathologySampledParameters(**sampled.to_response_dict()).model_dump(),
            "placement": PathologyPlacementInfo(**placement.as_dict()).model_dump(),
        }
        _set_cached_pathology_preview_response(response_cache_key, response_payload)

        return PathologyNoduleOnDicomResponse(
            **response_payload,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Pathology DICOM nodule preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Pathology DICOM nodule preview failed: {str(e)[:500]}",
        )


# ---------------------------------------------------------------------------
# Debug: lesion simulation diagnostics
# ---------------------------------------------------------------------------


@router.post("/debug-lesion", response_model=DebugLesionResponse)
async def debug_lesion(request: DebugLesionRequest):
    """
    Comprehensive lesion simulation debug endpoint.

    Runs the full lesion pipeline (generation → write) and returns
    detailed diagnostics to help pinpoint where bugs occur:

    - Task 1: Lesion generation statistics (voxel count, HU stats)
    - Task 2: Write verification (changed voxels, delta stats)
    - Task 3: Position validation (bounding box, inside-volume check)
    - Task 4: Spacing conversion analysis
    - Task 5: Preview PNG (axial slice at lesion center, base64)

    Returns a JSON object with all diagnostic fields.
    Does NOT create a simulation job or write any files.
    """
    try:
        # ── Resolve volume parameters ──
        volume_shape = tuple(request.volume_shape or [64, 128, 128])
        spacing = tuple(request.spacing or [1.0, 0.5, 0.5])

        # Normalize center: if all zeros, place at volume center
        cz, cy, cx = request.center_z, request.center_y, request.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume_shape[0] // 2)
            cy = float(volume_shape[1] // 2)
            cx = float(volume_shape[2] // 2)

        # ── Build base volume ──
        base_volume = np.full(volume_shape, -1000.0, dtype=np.float32)

        # ── Generate lesion ──
        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }

        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=volume_shape,
            config=config_dict,
            spacing=spacing,
        )
        lesion_mask = lesion_vol != 0

        # ── Task 1: Lesion generation stats ──
        lesion_voxels = int(np.sum(lesion_mask))
        lesion_ratio = float(lesion_voxels / max(volume_shape[0] * volume_shape[1] * volume_shape[2], 1))
        if lesion_voxels > 0:
            lesion_hu = lesion_vol[lesion_mask]
            lesion_hu_mean = float(np.mean(lesion_hu))
            lesion_hu_min = float(np.min(lesion_hu))
            lesion_hu_max = float(np.max(lesion_hu))
            lesion_hu_std = float(np.std(lesion_hu))
        else:
            lesion_hu_mean = lesion_hu_min = lesion_hu_max = lesion_hu_std = 0.0

        # ── Task 2: Write verification ──
        result_volume = base_volume.copy()
        result_volume[lesion_mask] = lesion_vol[lesion_mask]
        delta = result_volume.astype(np.float32) - base_volume.astype(np.float32)
        changed_voxels = int(np.count_nonzero(delta))
        write_delta_mean = float(np.mean(delta))
        write_delta_max = float(np.max(delta))

        # ── Task 3: Position ──
        nonzero = np.argwhere(lesion_mask)
        if len(nonzero) > 0:
            bbox = {
                "z_min": int(nonzero[:, 0].min()),
                "z_max": int(nonzero[:, 0].max()),
                "y_min": int(nonzero[:, 1].min()),
                "y_max": int(nonzero[:, 1].max()),
                "x_min": int(nonzero[:, 2].min()),
                "x_max": int(nonzero[:, 2].max()),
            }
            inside_volume = (
                bbox["z_min"] >= 0 and bbox["z_max"] < volume_shape[0]
                and bbox["y_min"] >= 0 and bbox["y_max"] < volume_shape[1]
                and bbox["x_min"] >= 0 and bbox["x_max"] < volume_shape[2]
            )
        else:
            bbox = {}
            inside_volume = False

        # ── Task 4: Spacing ──
        radius_voxel = [
            request.radius_z / spacing[0],
            request.radius_y / spacing[1],
            request.radius_x / spacing[2],
        ]
        z_compression_warning = radius_voxel[0] < 2.0

        # ── Task 5: Preview PNG (if matplotlib available) ──
        preview_png_base64 = None
        if HAS_MPL and lesion_voxels > 0:
            cz_idx = int(np.median(nonzero[:, 0])) if len(nonzero) > 0 else int(round(cz))
            cz_idx = max(0, min(cz_idx, volume_shape[0] - 1))

            fig, axes = plt.subplots(2, 2, figsize=(12, 10))
            axes = axes.ravel()

            # Mask
            im0 = axes[0].imshow(lesion_mask[cz_idx, :, :].astype(np.uint8) * 255, cmap="gray", aspect="equal")
            axes[0].set_title(f"Lesion Mask (axial z={cz_idx})")
            plt.colorbar(im0, ax=axes[0], shrink=0.75)

            # Lesion HU
            im1 = axes[1].imshow(lesion_vol[cz_idx, :, :], cmap="viridis", aspect="equal")
            axes[1].set_title(f"Lesion HU (axial z={cz_idx})")
            plt.colorbar(im1, ax=axes[1], shrink=0.75)

            # Result volume
            im2 = axes[2].imshow(result_volume[cz_idx, :, :], cmap="gray", aspect="equal")
            axes[2].set_title(f"Result HU (axial z={cz_idx})")
            plt.colorbar(im2, ax=axes[2], shrink=0.75)

            # Difference map
            diff = np.abs(result_volume.astype(np.float32) - base_volume.astype(np.float32))
            im3 = axes[3].imshow(diff[cz_idx, :, :], cmap="hot", aspect="equal")
            axes[3].set_title(f"|Δ HU| (axial z={cz_idx})")
            plt.colorbar(im3, ax=axes[3], shrink=0.75)

            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
            plt.close(fig)
            preview_png_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return DebugLesionResponse(
            lesion_voxels=lesion_voxels,
            lesion_ratio=lesion_ratio,
            lesion_hu_mean=lesion_hu_mean,
            lesion_hu_min=lesion_hu_min,
            lesion_hu_max=lesion_hu_max,
            lesion_hu_std=lesion_hu_std,
            changed_voxels=changed_voxels,
            write_delta_mean=write_delta_mean,
            write_delta_max=write_delta_max,
            volume_shape=list(volume_shape),
            center_voxel=[cz, cy, cx],
            bbox=bbox,
            inside_volume=inside_volume,
            spacing=list(spacing),
            radius_mm=[request.radius_z, request.radius_y, request.radius_x],
            radius_voxel=radius_voxel,
            z_compression_warning=z_compression_warning,
            preview_png_base64=preview_png_base64,
        )

    except Exception as e:
        logger.exception("Debug lesion endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Debug lesion failed: {str(e)[:500]}",
        )


# ---------------------------------------------------------------------------
# P3: Lesion Analysis — morphology and density statistics
# ---------------------------------------------------------------------------


@router.post("/lesion/analyze", response_model=LesionAnalysisResponse)
async def analyze_lesion(request: LesionAnalysisRequest):
    """
    Generate (or accept) a lesion and return comprehensive morphology metrics.

    Two modes:
      1. **Generate** (default): Provide lesion parameters (type, shape, radii, HU),
         a temporary volume is created, a lesion generated, and analyzed.
      2. **Existing data**: Provide ``volume_data_base64`` + ``mask_data_base64``
         to analyze a pre-existing lesion without re-generation.

    Returns:
        voxel_count, volume_mm3, max_diameter_mm, diameters_mm (z/y/x),
        hu_mean, hu_std, hu_min, hu_max,
        surface_area_mm2, sphericity, bbox, shape_info
    """
    from app.simulation.lesion.analyzer import analyze as analyze_lesion_volume
    from app.simulation.lesion.generator import LesionGenerator

    try:
        # ── Mode 2: Analyze existing data ──
        if request.volume_data_base64 and request.mask_data_base64:
            vol_bytes = base64.b64decode(request.volume_data_base64)
            mask_bytes = base64.b64decode(request.mask_data_base64)

            volume_shape = tuple(request.volume_shape or [64, 128, 128])
            spacing = tuple(request.spacing or [1.0, 0.5, 0.5])

            hu_volume = np.frombuffer(vol_bytes, dtype=np.float32).reshape(volume_shape)
            lesion_mask = np.frombuffer(mask_bytes, dtype=np.uint8).reshape(volume_shape) > 0

            result = analyze_lesion_volume(lesion_mask, hu_volume, spacing)
            return _analysis_to_response(result)

        # ── Mode 1: Generate then analyze ──
        volume_shape = tuple(request.volume_shape or [64, 128, 128])
        spacing = tuple(request.spacing or [1.0, 0.5, 0.5])

        # Normalise center
        cz, cy, cx = request.center_z, request.center_y, request.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume_shape[0] // 2)
            cy = float(volume_shape[1] // 2)
            cx = float(volume_shape[2] // 2)

        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }

        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=volume_shape,
            config=config_dict,
            spacing=spacing,
        )
        lesion_mask = lesion_vol != 0
        base_volume = np.full(volume_shape, -1000.0, dtype=np.float32)
        base_volume[lesion_mask] = lesion_vol[lesion_mask]

        result = analyze_lesion_volume(lesion_mask, base_volume, spacing)
        return _analysis_to_response(result)

    except Exception as e:
        logger.exception("Lesion analysis endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Lesion analysis failed: {str(e)[:500]}",
        )


def _analysis_to_response(analysis: dict) -> LesionAnalysisResponse:
    """Convert the analyzer dict to a Pydantic response."""
    from app.schemas.simulation import (
        LesionAnalysisResponse, DiametersMM, BBox,
    )
    b = analysis.get("bbox", {})
    return LesionAnalysisResponse(
        voxel_count=analysis.get("voxel_count", 0),
        volume_mm3=analysis.get("volume_mm3", 0.0),
        max_diameter_mm=analysis.get("max_diameter_mm", 0.0),
        diameters_mm=DiametersMM(**analysis.get("diameters_mm", {"z": 0, "y": 0, "x": 0})),
        hu_mean=analysis.get("hu_mean", 0.0),
        hu_std=analysis.get("hu_std", 0.0),
        hu_min=analysis.get("hu_min", 0.0),
        hu_max=analysis.get("hu_max", 0.0),
        surface_area_mm2=analysis.get("surface_area_mm2", 0.0),
        sphericity=analysis.get("sphericity", 0.0),
        bbox=BBox(
            z_min=b.get("z_min", 0), z_max=b.get("z_max", 0),
            y_min=b.get("y_min", 0), y_max=b.get("y_max", 0),
            x_min=b.get("x_min", 0), x_max=b.get("x_max", 0),
        ),
        shape_info=analysis.get("shape_info", "empty"),
    )


@router.post("/preview/organ", response_model=SimulationPreviewResponse)
async def preview_organ(config: dict):
    """
    Generate a fast preview of an organ configuration.

    Synchronous endpoint for real-time preview of organ parameters
    without creating a full simulation job.
    """
    try:
        simulator = OrganSimulator()
        preview = simulator.generate_preview(config)
        return SimulationPreviewResponse(
            job_id=str(uuid.uuid4()),
            preview_data=preview,
            voxel_count=preview.get("voxel_count", 0),
            hu_range=(preview.get("hu_min", 0), preview.get("hu_max", 0)),
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Preview generation failed: {str(e)}",
        )


# ---------------------------------------------------------------------------
# CT Phantom 鈥?synthetic upper-body CT volume for frontend demo
# ---------------------------------------------------------------------------


@router.post("/ct-params/preview", response_model=CTParamsPreviewResponse)
async def preview_ct_scan_params(
    request: CTParamsPreviewRequest,
    db: Session = Depends(get_db),
):
    """
    Generate a CT parameter preview for atlas, procedural, or uploaded DICOM CT volumes.

    The frontend already has access to the original phantom volume, so this
    endpoint returns only the simulated volume plus metadata and params_json.
    """
    try:
        if request.source not in {"atlas", "procedural", "dicom"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported source for CT parameter preview: {request.source}",
            )

        label_volume = None
        source_origin: Optional[Any] = None
        source_direction: Optional[Any] = None
        body_part = "upper_body"
        standardized_notes = list(DEFAULT_STANDARDIZED_NOTES)
        extra_metadata: Dict[str, Any] = {}

        if request.source == "atlas":
            source_case_id = request.case_id or "LUNG1-001"
            ct_volume, label_volume, phantom_metadata = generate_atlas_ct_phantom(
                case_id=source_case_id,
                size=request.size,
                scan_direction=request.scan_direction,
            )
            source_spacing = tuple(phantom_metadata.get("spacing", (1.0, 1.0, 1.0)))
            source_origin = phantom_metadata.get("origin")
            source_direction = phantom_metadata.get("direction")
            if source_origin is None or _normalize_origin(source_origin) == [0.0, 0.0, 0.0]:
                standardized_notes.append(
                    "origin uses default [0, 0, 0] because atlas origin is not propagated in this preview response."
                )
            else:
                standardized_notes.append(
                    "origin is propagated from the source atlas volume."
                )
            if source_direction is None or _normalize_direction(source_direction) == _identity_direction_matrix():
                standardized_notes.append(
                    "direction uses identity matrix because atlas direction is not propagated in this preview response."
                )
            else:
                standardized_notes.append(
                    "direction is propagated from the source atlas volume."
                )
            extra_metadata = {
                "case_id": source_case_id,
                "scan_direction": request.scan_direction,
                "spatial_reference": phantom_metadata.get("spatial_reference", "local_volume_space"),
                "phantom_metadata": {
                    "original_shape": phantom_metadata.get("original_shape"),
                    "output_shape": phantom_metadata.get("output_shape"),
                    "original_spacing": phantom_metadata.get("original_spacing"),
                    "output_spacing": phantom_metadata.get("output_spacing"),
                    "flipped_z": phantom_metadata.get("flipped_z"),
                },
            }
        elif request.source == "procedural":
            source_case_id = "procedural"
            ct_volume, label_volume, phantom_metadata = generate_procedural_ct_phantom(
                size=request.size,
            )
            source_spacing = tuple(phantom_metadata.get("spacing", (1.0, 1.0, 1.0)))
            standardized_notes.extend([
                "origin uses default [0, 0, 0] because procedural phantom origin is not propagated in this preview response.",
                "direction uses identity matrix because procedural phantom direction is not propagated in this preview response.",
            ])
            extra_metadata = {
                "case_id": None,
                "scan_direction": request.scan_direction,
                "spatial_reference": "local_volume_space",
                "phantom_metadata": {
                    "original_shape": phantom_metadata.get("original_shape"),
                    "output_shape": phantom_metadata.get("output_shape"),
                    "original_spacing": phantom_metadata.get("original_spacing"),
                    "output_spacing": phantom_metadata.get("output_spacing"),
                    "flipped_z": phantom_metadata.get("flipped_z"),
                },
            }
        else:
            if not request.series_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="series_id is required when source='dicom'",
                )

            series_query = db.query(DicomSeries).filter(DicomSeries.id == request.series_id)
            if request.study_id:
                series_query = series_query.filter(DicomSeries.study_id == request.study_id)
            series = series_query.first()
            if not series:
                if request.study_id:
                    raise HTTPException(
                        status_code=status.HTTP_404_NOT_FOUND,
                        detail=f"Series {request.series_id} not found in study {request.study_id}",
                    )
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Series {request.series_id} not found",
                )

            if series.modality and str(series.modality).upper() != "CT":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Series {request.series_id} modality '{series.modality}' is not supported for CT parameter preview",
                )

            instances = (
                db.query(DicomInstance)
                .filter(DicomInstance.series_id == series.id)
                .order_by(DicomInstance.instance_number.asc().nulls_last())
                .all()
            )
            if not instances:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No DICOM instances found for series {series.id}",
                )

            storage = get_storage_backend()
            try:
                ct_volume, dicom_metadata = build_volume_from_dicom(storage, instances)
            except ValueError as e:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Failed to build DICOM volume for series {series.id}: {str(e)}",
                ) from e
            except Exception as e:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to build DICOM volume for series {series.id}: {str(e)[:300]}",
                ) from e

            ct_volume, dicom_metadata = _normalize_dicom_scan_direction(
                ct_volume,
                dicom_metadata,
                request.scan_direction,
            )
            source_case_id = series.id
            source_spacing = tuple(dicom_metadata.get("spacing", (1.0, 1.0, 1.0)))
            source_origin = dicom_metadata.get("origin")
            source_direction = dicom_metadata.get("direction")
            body_part = series.body_part_examined or "unknown"
            original_shape = [int(dim) for dim in ct_volume.shape]
            original_spacing = [float(v) for v in source_spacing]
            ct_volume, resized_spacing, scale = _downsample_volume_to_max_dim(
                ct_volume,
                source_spacing,
                request.size,
                order=1,
            )
            source_spacing = resized_spacing

            if not series.modality:
                standardized_notes.append(
                    "DICOM series modality is missing in metadata; CT compatibility could not be verified beyond HU reconstruction."
                )
            if dicom_metadata.get("flipped_z"):
                standardized_notes.append(
                    f"DICOM z-axis was flipped to satisfy scan_direction={request.scan_direction} so z=0 matches the requested head/feet convention."
                )
            else:
                standardized_notes.append(
                    f"DICOM z-axis already matched scan_direction={request.scan_direction}; no z flip was applied."
                )
            if source_origin is None or _normalize_origin(source_origin) == [0.0, 0.0, 0.0]:
                standardized_notes.append(
                    "origin is unavailable from the current DICOM metadata path and therefore defaults to [0, 0, 0]."
                )
            else:
                standardized_notes.append(
                    "origin is propagated from DICOM ImagePositionPatient in patient XYZ space."
                )
            if source_direction is None or _normalize_direction(source_direction) == _identity_direction_matrix():
                standardized_notes.append(
                    "direction uses identity because DICOM ImageOrientationPatient was unavailable during volume reconstruction."
                )
            else:
                standardized_notes.append(
                    "direction is propagated from DICOM ImageOrientationPatient with volume-axis order z,y,x."
                )
            if not series.body_part_examined:
                standardized_notes.append(
                    "body_part defaults to 'unknown' because BodyPartExamined is missing in DICOM metadata."
                )

            extra_metadata = {
                "case_id": None,
                "study_id": series.study_id,
                "series_id": series.id,
                "scan_direction": request.scan_direction,
                "spatial_reference": dicom_metadata.get("spatial_reference", "dicom_patient_space"),
                "phantom_metadata": {
                    "original_shape": original_shape,
                    "output_shape": [int(ct_volume.shape[0]), int(ct_volume.shape[1]), int(ct_volume.shape[2])],
                    "original_spacing": original_spacing,
                    "output_spacing": [float(v) for v in resized_spacing],
                    "resample_scale": float(scale),
                    "flipped_z": bool(dicom_metadata.get("flipped_z", False)),
                },
                "dicom_metadata": {
                    "series_instance_uid": series.series_instance_uid,
                    "series_number": series.series_number,
                    "series_description": series.series_description,
                    "modality": series.modality,
                    "body_part_examined": series.body_part_examined,
                    "image_count": series.image_count,
                    "instance_count": len(instances),
                    "builder_num_slices": dicom_metadata.get("num_slices"),
                    "superior_component": dicom_metadata.get("dicom_superior_component"),
                },
            }

        simulation_result = simulate_ct_scan_params(
            volume=ct_volume,
            spacing=source_spacing,
            params=request.params.model_dump(),
            label_volume=label_volume,
        )

        simulated_volume = simulation_result["simulated_volume"]
        simulated_spacing = tuple(
            simulation_result.get(
                "simulated_spacing",
                extra_metadata.get("phantom_metadata", {}).get("output_spacing", source_spacing),
            )
        )
        metadata = {
            **simulation_result["metadata"],
            "source": request.source,
            "preview_stats": simulation_result["preview_stats"],
            **extra_metadata,
            "notes": standardized_notes,
        }
        params_json = {
            **simulation_result["params_json"],
            "notes": standardized_notes,
        }
        standardized_case = _build_standardized_ct_case(
            source=request.source,
            source_case_id=source_case_id,
            simulated_volume=simulated_volume,
            spacing=simulated_spacing,
            params_json=params_json,
            metadata=metadata,
            origin=source_origin,
            direction=source_direction,
            body_part=body_part,
        )

        volume_b64 = base64.b64encode(
            np.asarray(simulated_volume, dtype="<f4").tobytes()
        ).decode("ascii")
        simulated_label_volume = simulation_result.get("simulated_label_volume")
        simulated_label_b64 = None
        if simulated_label_volume is not None:
            simulated_label_b64 = base64.b64encode(
                np.asarray(simulated_label_volume, dtype=np.uint8).tobytes()
            ).decode("ascii")

        return CTParamsPreviewResponse(
            simulated_volume_base64=volume_b64,
            simulated_label_base64=simulated_label_b64,
            metadata=metadata,
            params_json=params_json,
            standardized_case=standardized_case,
        )

    except HTTPException:
        raise
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("CT parameter preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"CT parameter preview failed: {str(e)[:300]}",
        )


@router.get("/phantom")
async def generate_ct_phantom(
    source: str = Query("procedural", description="Phantom source: 'procedural', 'atlas', or 'dicom'"),
    size: int = Query(256, ge=64, le=320, description="Volume max edge size in voxels"),
    case_id: str = Query("LUNG1-001", description="Atlas case ID (only used when source='atlas')"),
    study_id: Optional[str] = Query(None, description="Study ID (used when source='dicom')"),
    series_id: Optional[str] = Query(None, description="Series ID (used when source='dicom')"),
    include_labels: bool = Query(True, description="Whether to include organ label volumes and metadata"),
    scan_direction: str = Query(
        "head_to_feet",
        description="Z-axis scan direction: 'head_to_feet' (z=0=head/chest) or 'feet_to_head'",
    ),
    db: Session = Depends(get_db),
):
    """
    Generate a CT phantom and return it as base64-encoded volume data.

    Three sources are supported:

    - **procedural** (default):
      Synthetic upper-body CT phantom built from geometric primitives
      (ellipses, arcs).  NOT a real medical image 鈥?suitable for UI
      development and demo.

    - **atlas**:
      Loads a real CT volume from models/phantom_atlas/{case_id}/.
      The CT is resampled so its largest dimension 鈮?size.  If an
      organs_label.nii.gz file is present it is converted to a uint8
      label map and returned alongside the CT volume.

      The z-axis direction is auto-detected from the NIfTI affine header.
      If it doesn't match `scan_direction` (default: head_to_feet), the
      volume is flipped so z=0 corresponds to the head/chest.

    - **dicom**:
      Loads a stored DICOM CT series, reconstructs the zyx volume in
      patient space, and downsamples it to at most `size` on the longest
      axis for workspace browsing performance.

    Returns:
        JSON with:
        - volumeBase64:  base64-encoded raw Float32 bytes (little-endian)
        - labelBase64:   base64-encoded raw Uint8 bytes (optional, atlas only)
        - metadata:      {width, height, depth, spacing, source, case_id?,
                          study_id?, series_id?, originalShape, outputShape, originalSpacing,
                          outputSpacing, scanAxis, scanDirection, flippedZ,
                          labelNonzeroCounts?, sliceLabelPresence?, origin?, direction?,
                          label_map?, windowPresets, bodyThresholdHU}
    """
    try:
        if source not in {"atlas", "procedural", "dicom"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unsupported phantom source: {source}",
            )

        if source == "dicom":
            if not series_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="series_id is required when source='dicom'",
                )

            series_query = db.query(DicomSeries).filter(DicomSeries.id == series_id)
            if study_id:
                series_query = series_query.filter(DicomSeries.study_id == study_id)
            series = series_query.first()
            if not series:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Series {series_id} not found",
                )
            if series.modality and str(series.modality).upper() != "CT":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Series {series_id} modality '{series.modality}' is not supported for CT workspace loading",
                )

            instances = (
                db.query(DicomInstance)
                .filter(DicomInstance.series_id == series.id)
                .order_by(DicomInstance.instance_number.asc().nulls_last())
                .all()
            )
            if not instances:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"No DICOM instances found for series {series.id}",
                )

            storage = get_storage_backend()
            volume, dicom_metadata = build_volume_from_dicom(storage, instances)
            volume, dicom_metadata = _normalize_dicom_scan_direction(
                volume,
                dicom_metadata,
                scan_direction,
            )
            source_spacing = tuple(dicom_metadata.get("spacing", (1.0, 1.0, 1.0)))
            original_shape = [int(dim) for dim in volume.shape]
            original_spacing = [float(v) for v in source_spacing]
            full_resolution_volume = volume
            full_resolution_spacing = source_spacing
            volume, resized_spacing, scale = _downsample_volume_to_max_dim(
                full_resolution_volume,
                source_spacing,
                size,
                order=1,
            )
            label_volume = None
            label_counts: Dict[int, int] = {}
            slice_label_presence: Dict[str, list] = {}
            seg_series_id = None
            label_source = None
            label_model_name = None
            label_error = None
            label_map: Dict[int, str] = {}
            if include_labels:
                segmentation_input_max_dim = max(size, 320)
                segmentation_input_volume = full_resolution_volume
                segmentation_input_spacing = full_resolution_spacing
                segmentation_input_scale = 1.0
                if max(full_resolution_volume.shape) > segmentation_input_max_dim:
                    (
                        segmentation_input_volume,
                        segmentation_input_spacing,
                        segmentation_input_scale,
                    ) = _downsample_volume_to_max_dim(
                        full_resolution_volume,
                        full_resolution_spacing,
                        segmentation_input_max_dim,
                        order=1,
                    )

                (
                    label_volume,
                    label_map,
                    label_counts,
                    slice_label_presence,
                    label_model_name,
                    label_error,
                ) = _load_nnunet_workspace_label_volume(
                    segmentation_input_volume,
                    segmentation_input_spacing,
                )
                if label_volume is not None:
                    label_volume = _resample_label_volume_to_shape(
                        label_volume,
                        (int(volume.shape[0]), int(volume.shape[1]), int(volume.shape[2])),
                    )
                    label_counts, slice_label_presence = _summarize_label_volume(label_volume, label_map)
                    label_source = "nnunet"
                else:
                    label_volume, label_counts, slice_label_presence, seg_series_id = _load_dicom_seg_label_volume(
                        db=db,
                        storage=storage,
                        study_id=series.study_id,
                        ct_series_id=series.id,
                        ct_instances=instances,
                        need_flip=bool(dicom_metadata.get("flipped_z", False)),
                        zoom_factor=float(scale),
                        output_shape=(int(volume.shape[0]), int(volume.shape[1]), int(volume.shape[2])),
                    )
                    label_source = "dicom_seg" if label_volume is not None else None
                    label_map = {int(k): v for k, v in LUNG_SAMPLE_LABEL_MAP.items()} if label_volume is not None else {}

            metadata = {
                "width": int(volume.shape[2]),
                "height": int(volume.shape[1]),
                "depth": int(volume.shape[0]),
                "spacing": [float(v) for v in resized_spacing],
                "source": "dicom",
                "study_id": series.study_id,
                "series_id": series.id,
                "series_description": series.series_description,
                "body_part_examined": series.body_part_examined,
                "modality": series.modality,
                "origin": _normalize_origin(dicom_metadata.get("origin")),
                "direction": _normalize_direction(dicom_metadata.get("direction")),
                "spatial_reference": dicom_metadata.get("spatial_reference", "dicom_patient_space"),
                "original_shape": original_shape,
                "output_shape": [int(volume.shape[0]), int(volume.shape[1]), int(volume.shape[2])],
                "original_spacing": original_spacing,
                "output_spacing": [float(v) for v in resized_spacing],
                "resample_scale": float(scale),
                "segmentation_input_shape": [
                    int(segmentation_input_volume.shape[0]),
                    int(segmentation_input_volume.shape[1]),
                    int(segmentation_input_volume.shape[2]),
                ] if include_labels else None,
                "segmentation_input_spacing": [float(v) for v in segmentation_input_spacing] if include_labels else None,
                "segmentation_input_scale": float(segmentation_input_scale) if include_labels else None,
                "scan_direction": scan_direction,
                "flipped_z": bool(dicom_metadata.get("flipped_z", False)),
                "segmentation_series_id": seg_series_id,
                "label_source": label_source,
                "label_model_name": label_model_name,
                "label_error": label_error,
                "label_map": {int(k): v for k, v in label_map.items()},
                "label_nonzero_counts": label_counts,
                "slice_label_presence": slice_label_presence,
                "window_presets": {
                    name: {"windowLevel": float(p["window_level"]), "windowWidth": float(p["window_width"])}
                    for name, p in WINDOW_PRESETS.items()
                },
                "body_threshold_hu": -500.0,
                "description": (
                    f"DICOM CT workspace volume from series {series.id}. "
                    f"Loaded in patient space and resized to shape {volume.shape[0]}x{volume.shape[1]}x{volume.shape[2]} "
                    f"for interactive browsing."
                ),
            }
            return JSONResponse(
                content=_build_workspace_volume_payload(
                    volume,
                    metadata,
                    label_volume=label_volume,
                    include_labels=include_labels,
                )
            )

        response_content = _get_cached_phantom_payload(
            source=source,
            size=size,
            case_id=case_id,
            scan_direction=scan_direction,
            include_labels=include_labels,
        )
        return JSONResponse(content=response_content)

    except FileNotFoundError as e:
        logger.warning("Phantom generation 鈥?file not found: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to generate CT phantom")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Phantom generation failed: {str(e)}",
        )


@router.get("/atlas-cases")
async def list_atlas_cases():
    """Return atlas case ids available for CT workspace loading."""
    case_ids = list_available_atlas_cases()
    return {
        "items": [
            {"case_id": case_id, "label": case_id}
            for case_id in case_ids
        ],
        "count": len(case_ids),
    }


# ---------------------------------------------------------------------------
# Export (placeholder 鈥?Phase 3 will implement real logic)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/export")
async def export_simulation_results(
    job_id: str,
    format: str = Query("dicom", description="Export format: dicom, nifti, nrrd"),
    db: Session = Depends(get_db),
):
    """
    Export simulation results in the specified format.

    Streams downloadable DICOM (zip), NIfTI (.nii.gz), or NRRD files
    containing the simulated lesions and organs.

    Returns:
        StreamingResponse with Content-Disposition attachment
    """
    job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation job {job_id} not found",
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not completed (current status: {job.status})",
        )

    if not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job output_path is empty 鈥?simulation did not complete successfully",
        )

    allowed_formats = ("dicom", "nifti", "nrrd")
    if format not in allowed_formats:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid format '{format}'. Allowed: {', '.join(allowed_formats)}",
        )

    storage = get_storage_backend()

    try:
        if format == "nrrd":
            return export_nrrd(job, storage)
        elif format == "nifti":
            return export_nifti(job, storage)
        elif format == "dicom":
            return export_dicom_zip(job, storage)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Export failed for job %s, format %s", job_id, format)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)[:200]}",
        )


