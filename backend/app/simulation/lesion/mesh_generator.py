"""
Mesh & Mask Lesion Generator
=============================

Generates lesion HU volumes from:
  - 3D mesh files (STL, OBJ, VTK, PLY) via vtk voxelization + SDF
  - Segmentation masks (NIfTI) via distance transform

Both modes produce output identical to LesionGenerator._generate_lesion_volume():
    np.ndarray of HU values with soft margins, shape (z, y, x)

Design:
  - MeshGenerator  → mesh → voxelize → signed-distance-field → sigmoid→HU
  - MaskGenerator  → NIfTI → resample → distance-transform   → sigmoid→HU
  - Neither replaces LesionGenerator; both are delegated-to helpers.

Dependencies (all already installed): vtk, nibabel, numpy, scipy
"""

import os
import logging
from typing import Dict, Any, Optional, Tuple

import numpy as np
from scipy.ndimage import distance_transform_edt, zoom

from app.core.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared utilities — HU assignment + soft margin
# (mirrors LesionGenerator._generate_lesion_volume() lines 333–343)
# ---------------------------------------------------------------------------

_HAVE_VTK = False
try:
    import vtk
    from vtk.util import numpy_support

    _HAVE_VTK = True
except ImportError:
    logger.warning("vtk not available — mesh-based lesion generation disabled")

_HAVE_NIBABEL = False
try:
    import nibabel as nib

    _HAVE_NIBABEL = True
except ImportError:
    logger.warning("nibabel not available — mask-based lesion generation disabled")


def _apply_hu_and_margin(
    distance: np.ndarray,
    hu_mean: float,
    hu_std: float,
    margin_sharpness: float,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Apply soft margin (sigmoid) and HU noise to a normalized distance field.

    Args:
        distance: Normalized distance field (0=center, 1=surface, >1=outside)
        hu_mean: Target mean HU value
        hu_std: Target HU standard deviation
        margin_sharpness: 0=diffuse, 1=sharp
        rng: NumPy random generator

    Returns:
        HU volume (float32) matching distance.shape
    """
    shape = distance.shape

    # Soft margin via sigmoid
    margin_width = 1.0 - margin_sharpness
    if margin_width > 0.01:
        mask = 1.0 / (1.0 + np.exp((distance - 1.0) / (margin_width * 0.2)))
    else:
        mask = (distance <= 1.0).astype(np.float32)

    # HU assignment with noise
    hu_volume = np.zeros(shape, dtype=np.float32)
    lesion_voxels = mask > 0.01
    hu_values = rng.normal(hu_mean, hu_std, shape)
    hu_volume[lesion_voxels] = hu_values[lesion_voxels].astype(np.float32)

    # Apply smooth transition at margins
    hu_volume = hu_volume * mask

    return hu_volume


# ---------------------------------------------------------------------------
# MeshGenerator
# ---------------------------------------------------------------------------


class MeshGenerator:
    """
    Generate a lesion HU volume from a 3D triangular mesh (STL/OBJ/VTK/PLY).

    Pipeline:
        load_mesh → scale+translate → voxelize → SDF → sigmoid → HU
    """

    SUPPORTED_EXTENSIONS = {".stl", ".obj", ".vtk", ".ply"}

    def __init__(self, seed: Optional[int] = None):
        if not _HAVE_VTK:
            raise RuntimeError(
                "vtk package is required for mesh-based lesion generation"
            )
        self.rng = np.random.default_rng(seed or settings.SIMULATION_DEFAULT_SEED)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_from_mesh(
        self,
        volume_shape: Tuple[int, int, int],
        mesh_path: str,
        config: Dict[str, Any],
    ) -> np.ndarray:
        """
        Generate a lesion HU volume from a mesh file.

        The mesh is scaled to fit within the radii specified in *config*
        and placed at the center specified in *config* (or auto-centered).

        Args:
            volume_shape: Target volume shape (z, y, x)
            mesh_path: Path to mesh file (.stl / .obj / .vtk / .ply)
            config: Lesion config dict (must contain hu_mean, hu_std,
                    margin_sharpness; optionally center_x/y/z, radius_x/y/z)

        Returns:
            HU volume (float32) of shape volume_shape
        """
        if not os.path.isfile(mesh_path):
            raise FileNotFoundError(f"Mesh file not found: {mesh_path}")

        # 1. Load mesh
        mesh = self._load_mesh(mesh_path)
        logger.info("Mesh loaded: %s (vertices=%d, polys=%d)",
                     mesh_path,
                     mesh.GetNumberOfPoints(),
                     mesh.GetNumberOfPolys())

        # 2. Resolve placement
        center, radii = self._resolve_placement(volume_shape, config)

        # 3. Compute signed distance field
        distance = self._compute_sdf(mesh, volume_shape, center, radii)

        # 4. Apply HU + margin
        hu_mean = config.get("hu_mean", 40.0)
        hu_std = config.get("hu_std", 20.0)
        margin_sharpness = config.get("margin_sharpness", 0.8)

        return _apply_hu_and_margin(distance, hu_mean, hu_std, margin_sharpness, self.rng)

    # ------------------------------------------------------------------
    # Internal: mesh loading
    # ------------------------------------------------------------------

    @staticmethod
    def _load_mesh(path: str) -> "vtk.vtkPolyData":
        """Load a mesh file, dispatching on extension."""
        ext = os.path.splitext(path)[1].lower()

        reader_map = {
            ".stl": vtk.vtkSTLReader,
            ".obj": vtk.vtkOBJReader,
            ".vtk": vtk.vtkPolyDataReader,
            ".ply": vtk.vtkPLYReader,
        }
        reader_cls = reader_map.get(ext)
        if reader_cls is None:
            raise ValueError(
                f"Unsupported mesh format '{ext}'. "
                f"Supported: {', '.join(sorted(MeshGenerator.SUPPORTED_EXTENSIONS))}"
            )

        reader = reader_cls()
        reader.SetFileName(path)
        reader.Update()

        output = reader.GetOutput()
        if output.GetNumberOfPoints() == 0:
            raise ValueError(f"Mesh file loaded 0 vertices: {path}")

        return output

    # ------------------------------------------------------------------
    # Internal: placement resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_placement(
        volume_shape: Tuple[int, int, int],
        config: Dict[str, Any],
    ) -> Tuple[Tuple[float, float, float], Tuple[float, float, float]]:
        """
        Extract center (z,y,x) and radii (voxels) from config.
        Same logic as LesionGenerator.generate_lesion() lines 129–143.
        """
        center = (
            max(0.0, min(config.get("center_z", float(volume_shape[0] // 2)),
                         float(volume_shape[0] - 1))),
            max(0.0, min(config.get("center_y", float(volume_shape[1] // 2)),
                         float(volume_shape[1] - 1))),
            max(0.0, min(config.get("center_x", float(volume_shape[2] // 2)),
                         float(volume_shape[2] - 1))),
        )
        vz = vy = vx = settings.SIMULATION_VOXEL_SIZE
        radii = (
            config.get("radius_z", 10) / vz,
            config.get("radius_y", 10) / vy,
            config.get("radius_x", 10) / vx,
        )
        return center, radii

    # ------------------------------------------------------------------
    # Internal: SDF via vtk
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sdf(
        mesh: "vtk.vtkPolyData",
        volume_shape: Tuple[int, int, int],
        center: Tuple[float, float, float],
        radii: Tuple[float, float, float],
    ) -> np.ndarray:
        """
        Compute a signed distance field of the mesh on the target grid.

        Pipeline:
          1. Scale mesh so its longest axis matches the target radii
          2. Translate mesh to target center
          3. Use vtkSampleFunction to evaluate SDF on the regular grid
          4. Convert to normalized distance (0=center, 1=surface, >1=outside)
        """
        nz, ny, nx = volume_shape
        cz, cy, cx = center
        rz, ry, rx = radii

        # ---- 1. Center mesh at origin ----
        bounds = mesh.GetBounds()
        mesh_cx = (bounds[0] + bounds[1]) / 2.0
        mesh_cy = (bounds[2] + bounds[3]) / 2.0
        mesh_cz = (bounds[4] + bounds[5]) / 2.0

        mesh_sx = max(bounds[1] - bounds[0], 1e-6)
        mesh_sy = max(bounds[3] - bounds[2], 1e-6)
        mesh_sz = max(bounds[5] - bounds[4], 1e-6)

        # Scale so that the mesh fills the target radii in each axis
        # (half-extent → radius)
        scale_x = rx / (mesh_sx / 2.0)
        scale_y = ry / (mesh_sy / 2.0)
        scale_z = rz / (mesh_sz / 2.0)

        # ---- 2. Build transform: origin-center → scale → translate ----
        transform = vtk.vtkTransform()
        transform.PostMultiply()
        transform.Translate(-mesh_cx, -mesh_cy, -mesh_cz)
        transform.Scale(scale_x, scale_y, scale_z)
        transform.Translate(cx, cy, cz)

        tf_filter = vtk.vtkTransformPolyDataFilter()
        tf_filter.SetInputData(mesh)
        tf_filter.SetTransform(transform)
        tf_filter.Update()
        transformed_mesh = tf_filter.GetOutput()

        # ---- 3. Evaluate SDF on a regular grid with vtkSampleFunction ----
        # The grid spans the full volume: x in [0, nx-1], y in [0, ny-1], z in [0, nz-1]
        implicit = vtk.vtkImplicitPolyDataDistance()
        implicit.SetInput(transformed_mesh)

        sample = vtk.vtkSampleFunction()
        sample.SetImplicitFunction(implicit)
        sample.SetSampleDimensions(nx, ny, nz)
        sample.SetModelBounds(
            0.0, float(nx - 1),
            0.0, float(ny - 1),
            0.0, float(nz - 1),
        )
        sample.ComputeNormalsOff()
        sample.Update()

        output = sample.GetOutput()
        vtk_array = output.GetPointData().GetScalars()
        raw_sdf = numpy_support.vtk_to_numpy(vtk_array)
        # vtkSampleFunction returns (nx * ny * nz) flat; reshape to (nx, ny, nz)
        raw_sdf = raw_sdf.reshape(nx, ny, nz)  # (x, y, z)

        # Transpose to (z, y, x) to match our convention
        raw_sdf = np.transpose(raw_sdf, (2, 1, 0))  # (z, y, x)

        # ---- 4. Convert SDF to normalized distance ----
        # raw_sdf: negative = inside mesh, 0 = surface, positive = outside
        # We want: 0 = center, ~1 = surface, >1 = outside
        # For the mesh interior, the most negative value is the "center"
        inside_mask = raw_sdf < 0
        if inside_mask.any():
            max_depth = float(np.abs(raw_sdf[inside_mask]).max())
            if max_depth < 1e-6:
                max_depth = 1.0
            distance = np.where(
                inside_mask,
                # Inside: 0 (deepest) → 1 (surface)
                1.0 - np.clip(-raw_sdf / max_depth, 0, 1),
                # Outside: 1 (surface) → larger
                1.0 + np.clip(raw_sdf / max_depth, 0, None),
            )
        else:
            # No interior voxels (should not happen for a valid mesh)
            distance = np.abs(raw_sdf) + 1.0

        # Safety clip
        distance = np.clip(distance, 0, None)

        logger.debug(
            "Mesh SDF: distance range [%.4f, %.4f], interior voxels=%d",
            float(distance.min()), float(distance.max()),
            int(np.sum(distance <= 1.0)),
        )
        return distance.astype(np.float32)


# ---------------------------------------------------------------------------
# MaskGenerator
# ---------------------------------------------------------------------------


class MaskGenerator:
    """
    Generate a lesion HU volume from a binary segmentation mask (NIfTI).

    Pipeline:
        load_mask → resample → distance-transform → sigmoid → HU

    The mask provides the lesion shape; HU values and margin are overlaid
    based on config (same as LesionGenerator).
    """

    def __init__(self, seed: Optional[int] = None):
        if not _HAVE_NIBABEL:
            raise RuntimeError(
                "nibabel package is required for mask-based lesion generation"
            )
        self.rng = np.random.default_rng(seed or settings.SIMULATION_DEFAULT_SEED)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_from_mask(
        self,
        volume_shape: Tuple[int, int, int],
        mask_path: str,
        config: Dict[str, Any],
    ) -> np.ndarray:
        """
        Generate a lesion HU volume from a NIfTI segmentation mask.

        The mask is resampled to *volume_shape* using nearest-neighbour
        interpolation, then a normalized distance field is computed via
        Euclidean distance transform.

        Args:
            volume_shape: Target volume shape (z, y, x)
            mask_path: Path to NIfTI mask file (.nii / .nii.gz / .img)
            config: Lesion config dict (hu_mean, hu_std, margin_sharpness)

        Returns:
            HU volume (float32) of shape volume_shape
        """
        if not os.path.isfile(mask_path):
            raise FileNotFoundError(f"Mask file not found: {mask_path}")

        # 1. Load + resample mask
        mask = self._load_mask(mask_path, volume_shape)

        if not mask.any():
            logger.warning("Mask is empty after loading: %s", mask_path)
            return np.zeros(volume_shape, dtype=np.float32)

        # 2. Compute normalized distance field
        distance = self._compute_distance(mask)

        # 3. Apply HU + margin
        hu_mean = config.get("hu_mean", 40.0)
        hu_std = config.get("hu_std", 20.0)
        margin_sharpness = config.get("margin_sharpness", 0.8)

        return _apply_hu_and_margin(distance, hu_mean, hu_std, margin_sharpness, self.rng)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    @staticmethod
    def _load_mask(
        path: str,
        target_shape: Tuple[int, int, int],
    ) -> np.ndarray:
        """Load a NIfTI mask and resample to target shape (nearest-neighbour)."""
        img = nib.load(path)
        data = img.get_fdata(dtype=np.float32)

        # Binarize: voxels > 0 are lesion
        binary = (data > 0).astype(np.float32)

        # Transpose from NIfTI (x, y, z) to our (z, y, x) if 3D
        if binary.ndim == 3:
            binary = np.transpose(binary, (2, 1, 0))

        # Resample if shape differs
        if binary.shape != target_shape:
            factors = [
                t / max(s, 1) for t, s in zip(target_shape, binary.shape)
            ]
            logger.info(
                "Resampling mask: %s → %s (factors=%s)",
                binary.shape, target_shape,
                [f"{f:.4f}" for f in factors],
            )
            binary = zoom(binary, factors, order=0).astype(np.float32)

        return binary

    @staticmethod
    def _compute_distance(mask: np.ndarray) -> np.ndarray:
        """
        Compute a normalized distance field from a binary mask.

        Returns:
            distance array (float32): 0 at interior core, ~1 at surface, >1 outside
        """
        # Euclidean distance to surface
        inside_dist = distance_transform_edt(mask)  # 0 at surface, max at core
        outside_dist = distance_transform_edt(~mask)  # 0 at surface

        max_inside = float(inside_dist.max())
        if max_inside < 1e-6:
            max_inside = 1.0  # degenerate mask — avoid division by zero

        distance = np.where(
            mask > 0,
            # Inside: 0 (core) → 1 (surface)
            np.clip(inside_dist / max_inside, 0, 1),
            # Outside: 1 (surface) → larger
            1.0 + np.clip(outside_dist / max_inside, 0, None),
        )

        distance = np.clip(distance, 0, None).astype(np.float32)

        logger.debug(
            "Mask distance field: range [%.4f, %.4f], interior voxels=%d",
            float(distance.min()), float(distance.max()),
            int(np.sum(mask > 0)),
        )
        return distance
