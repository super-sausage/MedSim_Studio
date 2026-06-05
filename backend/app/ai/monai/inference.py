"""
MONAI Inference Pipeline

Preprocessing, inference, and postprocessing for medical image segmentation.
Works with numpy arrays (z, y, x) and returns integer label maps.

Processing flow:
  1. Window HU values to [0, 1] using abdomen CT window
  2. Resample to isotropic spacing via MONAI Spacing transform
  3. Normalize (zero-mean, unit-variance)
  4. Model inference → logits → softmax → argmax
  5. Resample label map back to original spacing
  6. Filter out labels not in target list
"""

import logging
from typing import Dict, List, Optional, Tuple

import numpy as np

from app.ai.monai.model_loader import SegmentationModelManager, ModelNotAvailableError

logger = logging.getLogger(__name__)

# Default CT window for abdominal soft tissues (HU)
# Used to map raw HU values to the [0, 1] range expected by MONAI models
CT_WINDOW_CENTER = 40.0   # ~ soft tissue
CT_WINDOW_WIDTH = 350.0   # abdomen window
CT_WINDOW_MIN = CT_WINDOW_CENTER - CT_WINDOW_WIDTH / 2  # -135
CT_WINDOW_MAX = CT_WINDOW_CENTER + CT_WINDOW_WIDTH / 2  #  215

# Patch size for sliding-window inference (avoids OOM on large volumes)
PATCH_SIZE = (64, 128, 128)  # (z, y, x)
PATCH_OVERLAP = 0.25


def run_segmentation(
    volume: np.ndarray,
    model_name: str = "unet",
    target_organs: Optional[List[str]] = None,
    spacing: Optional[Tuple[float, float, float]] = None,
) -> np.ndarray:
    """
    Run MONAI model inference on a CT volume.

    Args:
        volume: 3D numpy array of HU values, shape (z, y, x)
        model_name: Model identifier ("unet", "segresnet", "swin_unetr")
        target_organs: List of organ names to keep; None = keep all
        spacing: (z, y, x) voxel spacing in mm; None = assume isotropic

    Returns:
        Label map: 3D int32 array of shape (z, y, x) with label indices

    Raises:
        ModelNotAvailableError: If torch/MONAI not installed
    """
    try:
        import torch
    except ImportError:
        raise ModelNotAvailableError(
            "PyTorch is required for segmentation. Install: pip install torch==2.1.2"
        )

    import time as _time
    _t0 = _time.time()

    manager = SegmentationModelManager()
    model = manager.load_model(model_name)
    logger.info("[INFER] Model loaded in %.1fs", _time.time() - _t0)

    # --- 1. Preprocess volume ---
    _t1 = _time.time()
    processed, orig_shape, orig_spacing = _preprocess(
        volume, spacing or (1.0, 1.0, 1.0), model_name
    )
    logger.info(
        "[INFER] Preprocessed: input=%s -> inference=%s (%.1fs)",
        orig_shape, processed.shape[2:], _time.time() - _t1,
    )

    # --- 2. Run inference ---
    _t2 = _time.time()
    # processed shape: (1, 1, Z, Y, X) — batch, channel, spatial
    with torch.no_grad():
        # Sliding-window inference for large volumes
        if any(s > p for s, p in zip(processed.shape[2:], PATCH_SIZE)):
            logger.info("[INFER] Using sliding-window inference (patch=%s)", PATCH_SIZE)
            logits = _sliding_window_inference(model, processed)
        else:
            logger.info("[INFER] Direct inference (volume fits in one pass)")
            device = next(model.parameters()).device
            input_tensor = torch.from_numpy(processed).to(device)
            logits = model(input_tensor)
            logits = logits.cpu().numpy()
    logger.info("[INFER] Forward pass complete (%.1fs)", _time.time() - _t2)

    # --- 3. Postprocess ---
    _t3 = _time.time()
    # logits shape: (1, C, Z, Y, X)
    probabilities = _softmax(logits)  # (1, C, Z, Y, X)
    label_map = np.argmax(probabilities, axis=1).astype(np.int32)  # (1, Z, Y, X)
    label_map = label_map[0]  # (Z, Y, X)
    unique_labels = np.unique(label_map)
    logger.info("[INFER] Softmax+argmax done, unique labels in result: %s (%.1fs)",
                unique_labels, _time.time() - _t3)

    # --- 4. Resample back to original spacing ---
    _t4 = _time.time()
    if orig_shape != label_map.shape:
        logger.info("[INFER] Resampling label_map from %s back to original %s...",
                    label_map.shape, orig_shape)
        label_map = _resample_label_map(label_map, orig_spacing, orig_shape)
        logger.info("[INFER] Resampled back to original shape=%s (%.1fs)",
                    orig_shape, _time.time() - _t4)

    # --- 5. Filter target organs ---
    _t5 = _time.time()
    if target_organs:
        from app.ai.monai.model_loader import ORGAN_LABEL_MAP
        valid_indices = {0}  # always keep background
        for organ in target_organs:
            idx = ORGAN_LABEL_MAP.get(organ.lower())
            if idx is not None:
                valid_indices.add(idx)
        mask = np.isin(label_map, list(valid_indices))
        label_map = label_map * mask
        logger.info("[INFER] Filtered to target organs=%s, unique now=%s (%.1fs)",
                    target_organs, np.unique(label_map), _time.time() - _t5)

    logger.info("[INFER] Final label_map shape=%s dtype=%s range=[%d, %d] unique=%s",
                label_map.shape, label_map.dtype,
                int(label_map.min()), int(label_map.max()),
                np.unique(label_map))
    return label_map


def _preprocess(
    volume: np.ndarray,
    spacing: Tuple[float, float, float],
    model_name: str,
) -> Tuple[np.ndarray, Tuple[int, int, int], Tuple[float, float, float]]:
    """
    Preprocess CT volume for MONAI model inference.

    Steps:
      1. Clip to abdomen CT window [-135, 215] HU and scale to [0, 1]
      2. Resample to isotropic ~1.5mm spacing
      3. Normalize to zero-mean, unit-variance
      4. Add batch and channel dims → (1, 1, Z, Y, X)

    Returns:
        (processed_tensor, original_shape, original_spacing)
    """
    orig_shape = volume.shape
    orig_spacing = spacing

    # Step 1: Window HU values
    hu_clipped = np.clip(volume, CT_WINDOW_MIN, CT_WINDOW_MAX)
    hu_normalized = (hu_clipped - CT_WINDOW_MIN) / (CT_WINDOW_MAX - CT_WINDOW_MIN)

    # Step 2: Resample to isotropic spacing
    target_spacing = _get_target_spacing(model_name)
    if spacing != target_spacing:
        resampled = _resample_volume(hu_normalized, spacing, target_spacing)
    else:
        resampled = hu_normalized

    # Step 3: Normalize
    mean = resampled.mean()
    std = resampled.std() + 1e-8
    normalized = (resampled - mean) / std

    # Step 4: Add batch/channel dimensions
    processed = normalized[np.newaxis, np.newaxis, ...].astype(np.float32)

    return processed, orig_shape, orig_spacing


def _get_target_spacing(model_name: str) -> Tuple[float, float, float]:
    """Return the expected voxel spacing for a given model."""
    # Most pretrained MONAI models expect ~1.5mm isotropic
    return (1.5, 1.5, 1.5)


def _resample_volume(
    volume: np.ndarray,
    current_spacing: Tuple[float, float, float],
    target_spacing: Tuple[float, float, float],
) -> np.ndarray:
    """Resample a 3D numpy volume to a new spacing using simple interpolation."""
    from scipy.ndimage import zoom as ndimage_zoom

    factors = (
        current_spacing[0] / target_spacing[0],
        current_spacing[1] / target_spacing[1],
        current_spacing[2] / target_spacing[2],
    )

    # Clamp to avoid extreme upsampling
    factors = tuple(max(0.25, min(f, 4.0)) for f in factors)

    resampled = ndimage_zoom(volume, factors, order=1)  # linear interpolation
    return resampled.astype(volume.dtype)


def _resample_label_map(
    label_map: np.ndarray,
    target_spacing: Tuple[float, float, float],
    target_shape: Tuple[int, int, int],
) -> np.ndarray:
    """Resample a label map back to original spacing using nearest-neighbor."""
    from scipy.ndimage import zoom as ndimage_zoom

    factors = (
        target_shape[0] / label_map.shape[0],
        target_shape[1] / label_map.shape[1],
        target_shape[2] / label_map.shape[2],
    )

    resampled = ndimage_zoom(label_map.astype(np.float32), factors, order=0)
    return np.round(resampled).astype(np.int32)


def _sliding_window_inference(
    model: object,
    input_tensor: np.ndarray,
) -> np.ndarray:
    """
    Run sliding-window inference for volumes larger than PATCH_SIZE.

    Splits the input into overlapping patches, runs inference on each,
    and stitches results together with averaging in overlap regions.
    """
    import torch

    _, _, Z, Y, X = input_tensor.shape
    pz, py, px = PATCH_SIZE
    overlap_z = int(pz * PATCH_OVERLAP)
    overlap_y = int(py * PATCH_OVERLAP)
    overlap_x = int(px * PATCH_OVERLAP)
    stride_z = pz - overlap_z
    stride_y = py - overlap_y
    stride_x = px - overlap_x

    # Output accumulator and weight map
    output = np.zeros((1, NUM_CLASSES, Z, Y, X), dtype=np.float32)
    weight_map = np.zeros((1, 1, Z, Y, X), dtype=np.float32)

    # Gaussian weighting for smooth blending
    weight_patch = _gaussian_weights((pz, py, px))

    for z_start in range(0, Z, stride_z):
        z_end = min(z_start + pz, Z)
        for y_start in range(0, Y, stride_y):
            y_end = min(y_start + py, Y)
            for x_start in range(0, X, stride_x):
                x_end = min(x_start + px, X)

                patch = input_tensor[
                    :, :,
                    z_start:z_end,
                    y_start:y_end,
                    x_start:x_end,
                ]

                # Pad if at boundary
                zd, yd, xd = z_end - z_start, y_end - y_start, x_end - x_start
                if zd < pz or yd < py or xd < px:
                    pad = ((0, 0), (0, 0), (0, pz - zd), (0, py - yd), (0, px - xd))
                    patch = np.pad(patch, pad, mode="constant", constant_values=0)
                    w = _gaussian_weights((pz, py, px))
                else:
                    w = weight_patch

                tensor = torch.from_numpy(patch).to(next(model.parameters()).device)
                with torch.no_grad():
                    logits_patch = model(tensor).cpu().numpy()

                # Crop back if padded
                if zd < pz or yd < py or xd < px:
                    logits_patch = logits_patch[:, :, :zd, :yd, :xd]
                    w = w[:zd, :yd, :xd]

                output[:, :, z_start:z_end, y_start:y_end, x_start:x_end] += logits_patch * w
                weight_map[:, :, z_start:z_end, y_start:y_end, x_start:x_end] += w

    # Avoid division by zero
    weight_map = np.clip(weight_map, 1e-8, None)
    output = output / weight_map
    return output


def _gaussian_weights(shape: Tuple[int, int, int]) -> np.ndarray:
    """Create 3D Gaussian weighting kernel for smooth patch blending."""
    z, y, x = shape
    z_center, y_center, x_center = (z - 1) / 2, (y - 1) / 2, (x - 1) / 2
    sigma = max(z, y, x) / 3.0

    Z, Y, X = np.ogrid[:z, :y, :x]
    dist = (
        ((Z - z_center) / sigma) ** 2
        + ((Y - y_center) / sigma) ** 2
        + ((X - x_center) / sigma) ** 2
    )
    weights = np.exp(-0.5 * dist)
    return weights.astype(np.float32)


def _softmax(logits: np.ndarray, axis: int = 1) -> np.ndarray:
    """Compute softmax along the specified axis."""
    max_val = np.max(logits, axis=axis, keepdims=True)
    exp = np.exp(logits - max_val)
    return exp / np.sum(exp, axis=axis, keepdims=True)


# Import NUM_CLASSES from model_loader for sliding window
from app.ai.monai.model_loader import NUM_CLASSES


def run_lesion_detection(
    volume: np.ndarray,
    model_name: str = "segresnet",
    spacing: Optional[Tuple[float, float, float]] = None,
) -> np.ndarray:
    """
    Run lesion-specific detection (tumor/metastasis).

    Args:
        volume: 3D numpy array of HU values, shape (z, y, x)
        model_name: Model to use (segresnet recommended for lesions)
        spacing: (z, y, x) voxel spacing in mm

    Returns:
        Binary label map where non-zero voxels indicate lesions.
        Index 8 = lesion_tumor, Index 9 = lesion_metastasis
    """
    label_map = run_segmentation(volume, model_name=model_name, spacing=spacing)

    # Zero out organ labels, keep only lesion labels (8, 9)
    organ_indices = set(range(1, 8))
    mask = np.isin(label_map, list(organ_indices))
    label_map[mask] = 0

    return label_map
