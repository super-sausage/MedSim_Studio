"""
Volume Builder

Builds 3D CT volumes from DICOM series (read from storage backend)
or generates synthetic base volumes when no source DICOM is provided.

Responsibilities:
- Read DICOM instances from storage backend
- Parse pixel data with HU conversion (RescaleSlope / RescaleIntercept)
- Sort slices by ImagePositionPatient or slice_location
- Stack into 3D numpy array
- Generate synthetic base volume as fallback

Returns (volume_array, metadata_dict) where metadata_dict contains
spacing, origin, direction for use with SimpleITK.
"""

import io
import logging
from typing import Dict, Any, List, Optional, Tuple

import numpy as np
import pydicom

from app.dicom.storage.base import StorageBackend
from app.models.dicom import DicomInstance

logger = logging.getLogger(__name__)

# Default volume parameters for synthetic mode
DEFAULT_SHAPE = (64, 128, 128)  # (z, y, x)
DEFAULT_SPACING = (1.0, 0.5, 0.5)  # mm (z, y, x)
DEFAULT_ORIGIN = (0.0, 0.0, 0.0)


def build_volume_from_dicom(
    storage: StorageBackend,
    instances: List[DicomInstance],
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Build a 3D CT volume from DICOM instances stored in the storage backend.

    Reads each instance's pixel data via storage.get_object_bytes(),
    applies RescaleSlope / RescaleIntercept for HU conversion,
    sorts slices using ImagePositionPatient / ImageOrientationPatient when available,
    and stacks into a 3D numpy array.

    Args:
        storage: StorageBackend instance (MinIO or Local)
        instances: List of DicomInstance ORM objects, already ordered by instance_number

    Returns:
        Tuple of (volume_array, metadata_dict) where:
        - volume_array: np.ndarray of shape (z, y, x) with HU values (float32)
        - metadata_dict: {"spacing": (z,y,x), "origin": patient XYZ, "direction": tuple}

    Raises:
        ValueError: If no valid instances found or pixel data unreadable
    """
    if not instances:
        raise ValueError("No DICOM instances provided")

    # Collect slices with their geometry for sorting and metadata reconstruction.
    slices: List[Dict[str, Any]] = []
    slice_thickness: Optional[float] = None
    pixel_spacing: Optional[Tuple[float, float]] = None
    origin: Tuple[float, float, float] = DEFAULT_ORIGIN
    row_direction: Optional[np.ndarray] = None
    col_direction: Optional[np.ndarray] = None

    for inst in instances:
        if not inst.pixel_data_path:
            logger.warning("Instance %s has no pixel_data_path, skipping", inst.id)
            continue

        # Read DICOM bytes from storage backend
        dicom_bytes = storage.get_object_bytes(inst.pixel_data_path)
        if dicom_bytes is None:
            logger.warning(
                "Failed to read instance %s from storage (key=%s), skipping",
                inst.id, inst.pixel_data_path,
            )
            continue

        try:
            ds = pydicom.dcmread(io.BytesIO(dicom_bytes), force=True)
        except Exception as e:
            logger.warning("Failed to parse DICOM for instance %s: %s, skipping", inst.id, e)
            continue

        # Extract pixel data
        if not hasattr(ds, "pixel_array"):
            logger.warning("Instance %s has no pixel_array, skipping", inst.id)
            continue

        try:
            pixel_array = ds.pixel_array.astype(np.float32)
        except Exception as e:
            logger.warning("Failed to extract pixel_array for instance %s: %s", inst.id, e)
            continue

        # Apply RescaleSlope / RescaleIntercept for HU conversion
        rescale_slope = getattr(ds, "RescaleSlope", None)
        rescale_intercept = getattr(ds, "RescaleIntercept", None)
        if rescale_slope is not None:
            pixel_array = pixel_array * float(rescale_slope)
        if rescale_intercept is not None:
            pixel_array = pixel_array + float(rescale_intercept)

        image_position = _extract_image_position(ds, inst)
        image_orientation = _extract_image_orientation(ds, inst)

        if image_orientation is not None and row_direction is None and col_direction is None:
            row_direction = image_orientation[:3]
            col_direction = image_orientation[3:]

        slices.append(
            {
                "sort_fallback": _get_slice_z_position(ds, inst),
                "pixel_array": pixel_array,
                "image_position": image_position,
            }
        )

        # Capture metadata from first valid slice
        if slice_thickness is None:
            slice_thickness = _safe_float(getattr(ds, "SliceThickness", None))
        if pixel_spacing is None:
            ps = getattr(ds, "PixelSpacing", None)
            if ps is not None:
                try:
                    pixel_spacing = (float(ps[0]), float(ps[1]))
                except (IndexError, TypeError, ValueError):
                    pass

    if not slices:
        raise ValueError("No valid DICOM slices could be read from storage backend")

    slice_direction: Optional[np.ndarray] = None
    if row_direction is not None and col_direction is not None:
        slice_direction = np.cross(row_direction, col_direction)
        norm = float(np.linalg.norm(slice_direction))
        if norm > 1e-6:
            slice_direction = slice_direction / norm
        else:
            slice_direction = None

    has_complete_image_positions = all(s["image_position"] is not None for s in slices)

    if slice_direction is not None and has_complete_image_positions:
        for slice_info in slices:
            slice_info["sort_position"] = float(np.dot(slice_info["image_position"], slice_direction))
        slices.sort(key=lambda s: s["sort_position"])
    else:
        slices.sort(key=lambda s: s["sort_fallback"])

    # Stack into 3D volume: shape (z, y, x)
    volume = np.stack([s["pixel_array"] for s in slices], axis=0)

    # Compute spacing
    slice_step_estimates: List[float] = []
    if len(slices) > 1 and slice_direction is not None and has_complete_image_positions:
        for prev_slice, next_slice in zip(slices[:-1], slices[1:]):
            prev_pos = prev_slice["image_position"]
            next_pos = next_slice["image_position"]
            delta = next_pos - prev_pos
            projected_step = abs(float(np.dot(delta, slice_direction)))
            if projected_step > 1e-6:
                slice_step_estimates.append(projected_step)

    if slice_step_estimates:
        z_spacing = float(np.median(slice_step_estimates))
    elif len(slices) > 1:
        z_spacing = abs(float(slices[1]["sort_fallback"]) - float(slices[0]["sort_fallback"]))
        if z_spacing < 1e-6:
            z_spacing = slice_thickness or 1.0
    else:
        z_spacing = slice_thickness or 1.0

    y_spacing = pixel_spacing[0] if pixel_spacing else 1.0
    x_spacing = pixel_spacing[1] if pixel_spacing else 1.0

    spacing = (z_spacing, y_spacing, x_spacing)

    if slices and slices[0]["image_position"] is not None:
        first_position = slices[0]["image_position"]
        origin = (
            float(first_position[0]),
            float(first_position[1]),
            float(first_position[2]),
        )

    if row_direction is None or col_direction is None or slice_direction is None:
        direction = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    else:
        # Store direction in volume-axis order (z, y, x) for this zyx array.
        direction = (
            float(slice_direction[0]), float(slice_direction[1]), float(slice_direction[2]),
            float(row_direction[0]), float(row_direction[1]), float(row_direction[2]),
            float(col_direction[0]), float(col_direction[1]), float(col_direction[2]),
        )

    metadata = {
        "spacing": spacing,
        "origin": origin,
        "direction": direction,
        "num_slices": len(slices),
        "source": "dicom",
        "spatial_reference": "dicom_patient_space",
    }

    logger.info(
        "Built DICOM volume: shape=%s, spacing=%s, slices=%d",
        volume.shape, spacing, len(slices),
    )

    return volume, metadata


def build_synthetic_volume(
    shape: Tuple[int, int, int] = DEFAULT_SHAPE,
    spacing: Tuple[float, float, float] = DEFAULT_SPACING,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """
    Generate a synthetic base CT volume for simulation without source DICOM.

    Creates a volume with:
    - Air background (-1000 HU)
    - Central soft-tissue ellipsoid (~40 HU with noise)
    - Realistic spacing metadata

    This is the "no-source simulation mode" — used when a simulation job
    is created without a study_id/series_id reference.

    Args:
        shape: Volume shape (z, y, x)
        spacing: Voxel spacing in mm (z, y, x)

    Returns:
        Tuple of (volume_array, metadata_dict)
    """
    rng = np.random.default_rng(42)

    # Air background
    volume = np.full(shape, -1000.0, dtype=np.float32)

    # Central soft-tissue ellipsoid
    cz, cy, cx = shape[0] // 2, shape[1] // 2, shape[2] // 2
    rz, ry, rx = shape[0] * 0.35, shape[1] * 0.35, shape[2] * 0.35

    z, y, x = np.indices(shape, dtype=float)
    distance = np.sqrt(
        ((z - cz) / rz) ** 2 +
        ((y - cy) / ry) ** 2 +
        ((x - cx) / rx) ** 2
    )

    tissue_mask = distance <= 1.0

    # Soft tissue HU: ~40 ± 10
    tissue_hu = rng.normal(40.0, 10.0, shape).astype(np.float32)
    volume[tissue_mask] = tissue_hu[tissue_mask]

    # Smooth edges
    from scipy.ndimage import gaussian_filter
    edge_mask = (distance > 0.9) & (distance <= 1.1)
    if edge_mask.any():
        smooth_factor = 1.0 - (distance[edge_mask] - 0.9) / 0.2
        smooth_factor = np.clip(smooth_factor, 0, 1)
        volume[edge_mask] = volume[edge_mask] * smooth_factor + (-1000.0) * (1.0 - smooth_factor)

    metadata = {
        "spacing": spacing,
        "origin": DEFAULT_ORIGIN,
        "direction": (1, 0, 0, 0, 1, 0, 0, 0, 1),
        "num_slices": shape[0],
        "source": "synthetic",
    }

    logger.info(
        "Built synthetic volume: shape=%s, spacing=%s",
        shape, spacing,
    )

    return volume, metadata


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_slice_z_position(ds, inst: DicomInstance) -> float:
    """
    Determine the z-axis position of a DICOM slice.

    Priority:
    1. ImagePositionPatient[2] (most reliable)
    2. DicomInstance.slice_location (from DB metadata)
    3. DicomInstance.instance_number (fallback)
    """
    # Try ImagePositionPatient from DICOM dataset
    ipp = getattr(ds, "ImagePositionPatient", None)
    if ipp is not None:
        try:
            return float(ipp[2])
        except (IndexError, TypeError, ValueError):
            pass

    # Try from DB metadata
    if inst.slice_location is not None:
        return float(inst.slice_location)

    # Try from DB image_position JSON
    if inst.image_position is not None:
        try:
            if isinstance(inst.image_position, (list, tuple)) and len(inst.image_position) >= 3:
                return float(inst.image_position[2])
        except (IndexError, TypeError, ValueError):
            pass

    # Last resort: instance_number
    if inst.instance_number is not None:
        return float(inst.instance_number)

    return 0.0


def _extract_image_position(ds, inst: DicomInstance) -> Optional[np.ndarray]:
    """Return ImagePositionPatient as a float vector in patient XYZ space."""
    ipp = getattr(ds, "ImagePositionPatient", None)
    if ipp is not None:
        try:
            return np.array([float(ipp[0]), float(ipp[1]), float(ipp[2])], dtype=np.float64)
        except (IndexError, TypeError, ValueError):
            pass

    if inst.image_position is not None:
        try:
            if isinstance(inst.image_position, (list, tuple)) and len(inst.image_position) >= 3:
                return np.array(
                    [
                        float(inst.image_position[0]),
                        float(inst.image_position[1]),
                        float(inst.image_position[2]),
                    ],
                    dtype=np.float64,
                )
        except (IndexError, TypeError, ValueError):
            pass

    return None


def _extract_image_orientation(ds, inst: DicomInstance) -> Optional[np.ndarray]:
    """Return ImageOrientationPatient as six float direction cosines."""
    iop = getattr(ds, "ImageOrientationPatient", None)
    if iop is not None:
        try:
            return np.array([float(iop[idx]) for idx in range(6)], dtype=np.float64)
        except (IndexError, TypeError, ValueError):
            pass

    if inst.image_orientation is not None:
        try:
            if isinstance(inst.image_orientation, (list, tuple)) and len(inst.image_orientation) >= 6:
                return np.array([float(inst.image_orientation[idx]) for idx in range(6)], dtype=np.float64)
        except (IndexError, TypeError, ValueError):
            pass

    return None


def _safe_float(value, default: float = 1.0) -> float:
    """Safely convert a value to float."""
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        return default
