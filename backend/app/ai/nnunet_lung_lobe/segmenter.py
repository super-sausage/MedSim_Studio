"""
Custom nnUNet Lung Lobe Inference Wrapper (Dataset703_LungLobes, 5-class)

Loads the trained nnUNetv2 lung lobe model from a local checkpoint and runs
inference against a CT volume. Uses nnUNetPredictor's single-numpy-array
path so no intermediate files are needed.

The model segments 5 lung lobes:
  left_upper_lobe (1), left_lower_lobe (2),
  right_upper_lobe (3), right_middle_lobe (4), right_lower_lobe (5)

Architecture flow:
  1. Receive CT volume as numpy array (z, y, x)
  2. Transpose to nnUNet convention: (1, x, y, z)  [C=1 for CT]
  3. Run nnUNetPredictor.predict_single_npy_array
  4. Transpose back to (z, y, x)
  5. Return int32 label map
"""

import logging
import os
import shutil
import threading
from pathlib import Path
from typing import Any, Optional, Tuple

import numpy as np

from app.core.config import settings

logger = logging.getLogger(__name__)
_PREDICTOR_LOCK = threading.Lock()
_PREDICTOR_CACHE: dict[str, Any] = {}


class CustomModelNotAvailableError(RuntimeError):
    """Raised when the lung lobe model folder is not found."""
    pass


def is_available() -> bool:
    """Check that the trained model folder exists and contains a fold_0 subdir.

    Returns:
        True if the model checkpoint can be loaded, False otherwise.
    """
    model_folder = _get_model_folder()
    if not model_folder or not model_folder.exists():
        return False
    return True


def _get_model_folder() -> Optional[Path]:
    """Return the Path to the nnUNetTrainer__nnUNetPlans__3d_fullres folder.

    The expected layout on disk (after Docker volume mount):
      /app/models/nnunet_lung_lobe/
        dataset.json
        plans.json
        fold_0/
          checkpoint_best.pth
          ...
    """
    candidate = Path(settings.NNUNET_LUNG_LOBE_MODEL_PATH)
    if candidate.is_dir():
        if (candidate / "dataset.json").is_file() and (candidate / "fold_0").is_dir():
            return candidate

    # Fallback: search for the nnUNetTrainer__nnUNetPlans__3d_fullres subfolder
    for child in candidate.iterdir():
        if child.is_dir() and "nnUNetTrainer" in child.name:
            return child
    return None


def run_nnunet_lung_lobe(
    volume: np.ndarray,
    spacing: Tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> np.ndarray:
    """Run nnUNet lung lobe inference on a CT volume.

    Args:
        volume: 3D numpy array of HU values, shape (z, y, x)
        spacing: Voxel spacing in mm as (z_spacing, y_spacing, x_spacing)

    Returns:
        3D int32 label map with shape (z, y, x). Each voxel contains the
        label index:
          0 = background
          1 = left_upper_lobe
          2 = left_lower_lobe
          3 = right_upper_lobe
          4 = right_middle_lobe
          5 = right_lower_lobe

    Raises:
        CustomModelNotAvailableError: If the model folder is not found.
    """
    model_folder = _get_model_folder()
    if model_folder is None:
        raise CustomModelNotAvailableError(
            f"Lung lobe nnUNet model not found at "
            f"{settings.NNUNET_LUNG_LOBE_MODEL_PATH}. "
            "Make sure the model directory is mounted correctly."
        )

    if volume.ndim != 3:
        raise ValueError(f"Expected 3D volume (z, y, x), got shape {volume.shape}")
    if volume.size == 0:
        raise ValueError("Input volume is empty")

    # ---- Environment tuning (same as existing nnUNet modules) ----
    os.environ.setdefault("NUMEXPR_MAX_THREADS", "4")
    os.environ.setdefault("OMP_NUM_THREADS", "4")
    os.environ.setdefault("MKL_NUM_THREADS", "4")
    os.environ.setdefault("OPENBLAS_NUM_THREADS", "4")

    import torch
    import multiprocessing

    _orig_cpu_count = multiprocessing.cpu_count
    multiprocessing.cpu_count = lambda: 4
    torch.set_num_threads(4)

    is_cpu = settings.AI_DEVICE == "cpu"
    device = torch.device("cpu") if is_cpu else torch.device("cuda", 0)

    # ---- Transpose volume: (z, y, x) → (x, y, z) ----
    _t0 = __import__("time").time()
    vol_xyz = volume.transpose(2, 1, 0).astype(np.float32)       # (x, y, z)

    # nnUNet expects spacing in (x, y, z) order
    spacing_xyz = (spacing[2], spacing[1], spacing[0])

    logger.info(
        "[nnUNet-LungLobe] Starting inference: shape=%s spacing=%s device=%s",
        volume.shape, spacing, device,
    )

    # ---- Determine approach based on model size ----
    # Load dataset.json to check target spacing / patch size
    dataset_json_path = model_folder / "dataset.json"
    if dataset_json_path.is_file():
        import json
        with open(dataset_json_path) as f:
            ds = json.load(f)
        logger.info(
            "[nnUNet-LungLobe] Model info: %s, %d training cases",
            ds.get("name", "unknown"), ds.get("numTraining", 0),
        )

    # ---- Initialize nnUNetPredictor ----
    predictor = _get_or_create_predictor(model_folder, device)

    # ---- Run prediction ----
    _t2 = __import__("time").time()
    spacing_data = {
        "spacing": spacing_xyz,
    }

    prediction = predictor.predict_single_npy_array(
        vol_xyz[np.newaxis, ...],   # (1, x, y, z)
        spacing_data,
    )

    logger.info(
        "[nnUNet-LungLobe] Inference complete (%.1fs)",
        __import__("time").time() - _t2,
    )

    # ---- Transpose result back: (x, y, z) → (z, y, x) ----
    # predict_single_npy_array returns argmax, shape (x, y, z)
    label_map = prediction.astype(np.int32)   # (x, y, z)
    label_map = label_map.transpose(2, 1, 0)  # (z, y, x)

    # Handle shape mismatch from resampling
    if label_map.shape != volume.shape:
        logger.warning(
            "[nnUNet-LungLobe] Shape mismatch: output=%s expected=%s, resampling...",
            label_map.shape, volume.shape,
        )
        from scipy.ndimage import zoom
        factors = (
            volume.shape[0] / label_map.shape[0],
            volume.shape[1] / label_map.shape[1],
            volume.shape[2] / label_map.shape[2],
        )
        label_map = zoom(label_map.astype(np.float32), factors, order=0)
        label_map = np.round(label_map).astype(np.int32)

    logger.info(
        "[nnUNet-LungLobe] Complete (total %.1fs): shape=%s unique_labels=%s",
        __import__("time").time() - _t0,
        label_map.shape,
        np.unique(label_map),
    )

    return label_map


def warmup_nnunet_lung_lobe() -> None:
    """Load and cache the predictor during app startup."""
    model_folder = _get_model_folder()
    if model_folder is None:
        raise CustomModelNotAvailableError(
            f"Lung lobe nnUNet model not found at {settings.NNUNET_LUNG_LOBE_MODEL_PATH}. "
            "Make sure the model directory is mounted correctly."
        )

    import torch

    is_cpu = settings.AI_DEVICE == "cpu"
    device = torch.device("cpu") if is_cpu else torch.device("cuda", 0)
    _get_or_create_predictor(model_folder, device)


def _get_or_create_predictor(model_folder: Path, device: "Any") -> "Any":
    cache_key = f"{model_folder.resolve()}::{device}"
    cached = _PREDICTOR_CACHE.get(cache_key)
    if cached is not None:
        logger.info("[nnUNet-LungLobe] Reusing cached predictor for %s", model_folder.name)
        return cached

    with _PREDICTOR_LOCK:
        cached = _PREDICTOR_CACHE.get(cache_key)
        if cached is not None:
            logger.info("[nnUNet-LungLobe] Reusing cached predictor for %s", model_folder.name)
            return cached

        from nnunetv2.inference.predict_from_raw_data import nnUNetPredictor

        predictor = nnUNetPredictor(
            tile_step_size=0.5,
            use_gaussian=True,
            use_mirroring=True,
            perform_everything_on_device=True,
            device=device,
            verbose=False,
            verbose_preprocessing=False,
            allow_tqdm=False,
        )

        _t1 = __import__("time").time()
        predictor.initialize_from_trained_model_folder(
            str(model_folder),
            use_folds=("0",),
            checkpoint_name="checkpoint_best.pth",
        )
        logger.info(
            "[nnUNet-LungLobe] Model loaded (%.1fs)  trainer=%s",
            __import__("time").time() - _t1,
            model_folder.name,
        )
        _PREDICTOR_CACHE[cache_key] = predictor
        return predictor
