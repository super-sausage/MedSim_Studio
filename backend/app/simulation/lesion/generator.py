"""
Lesion Generator

Generates synthetic lesions in CT volumes with realistic
Hounsfield Unit distributions, shapes, and margins.

Supported lesion types:
- Tumor: Soft tissue density with heterogeneity
- Nodule: Small rounded opacity, typically in lung
- Cyst: Fluid-filled with near-water density
- Calcification: High-density calcium deposits
- Metastasis: Secondary malignant growth with irregular margins
"""

import logging
from typing import Dict, Any, Optional, Tuple
import numpy as np
from scipy.ndimage import gaussian_filter, binary_dilation, generate_binary_structure
from app.core.config import settings

logger = logging.getLogger(__name__)


class LesionGenerator:
    """
    Synthetic lesion generator for CT medical imaging.

    Generates realistic lesion volumes with configurable:
    - Shape (spherical, ellipsoidal, irregular, lobulated, spiculated)
    - HU density (mean and standard deviation)
    - Margin characteristics (sharp vs. diffuse)
    - Internal structure (calcification, necrosis, heterogeneity)
    """

    # Default HU ranges for different lesion types
    LESION_HU_DEFAULTS = {
        "tumor": {"mean": 40, "std": 20, "description": "Soft tissue tumor"},
        "nodule": {"mean": -100, "std": 50, "description": "Pulmonary nodule"},
        "cyst": {"mean": 10, "std": 5, "description": "Fluid-filled cyst"},
        "calcification": {"mean": 300, "std": 100, "description": "Calcified lesion"},
        "metastasis": {"mean": 50, "std": 25, "description": "Metastatic lesion"},
    }

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed or settings.SIMULATION_DEFAULT_SEED)

    def generate_preview(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Generate a fast preview of a lesion configuration.

        Returns summary statistics and a small voxel preview
        without generating the full volume.
        """
        center = (
            config.get("center_x", 0),
            config.get("center_y", 0),
            config.get("center_z", 0),
        )
        radii = (
            config.get("radius_x", 10),
            config.get("radius_y", 10),
            config.get("radius_z", 10),
        )

        # Generate small preview volume (32×32×32)
        preview_size = 32
        preview = self._generate_lesion_volume(
            shape=(preview_size, preview_size, preview_size),
            center=(preview_size // 2,) * 3,
            radii=(radii[0] * 0.3, radii[1] * 0.3, radii[2] * 0.3),
            hu_mean=config.get("hu_mean", 40),
            hu_std=config.get("hu_std", 20),
            margin_sharpness=config.get("margin_sharpness", 0.8),
            shape_type=config.get("shape", "spherical"),
            spiculation=config.get("spiculation_degree", 0.0),
        )

        return {
            "voxel_count": int(np.sum(preview > -500)),
            "hu_min": float(np.min(preview)),
            "hu_max": float(np.max(preview)),
            "hu_mean": float(np.mean(preview)),
            "hu_std": float(np.std(preview)),
            "volume_mm3": float(np.sum(preview > -500) * settings.SIMULATION_VOXEL_SIZE ** 3),
            "preview_shape": list(preview.shape),
            "center": list(center),
            "radii_mm": list(radii),
        }

    def generate_lesion(
        self,
        volume_shape: Tuple[int, int, int],
        config: Dict[str, Any],
        spacing: Optional[Tuple[float, float, float]] = None,
    ) -> np.ndarray:
        """
        Generate a lesion within a volume of the given shape.

        Args:
            volume_shape: Shape of the target volume (z, y, x)
            config: Lesion configuration dictionary
            spacing: Voxel spacing (z, y, x) in mm. Used to convert mm radii
                     to voxel counts. Falls back to SIMULATION_VOXEL_SIZE if None.

        Returns:
            numpy array of HU values matching volume_shape
        """
        lesion_type = config.get("lesion_type", "tumor")
        hu_defaults = self.LESION_HU_DEFAULTS.get(lesion_type, self.LESION_HU_DEFAULTS["tumor"])

        center = (
            config.get("center_z", volume_shape[0] // 2),
            config.get("center_y", volume_shape[1] // 2),
            config.get("center_x", volume_shape[2] // 2),
        )
        # Convert mm radii to voxel counts using actual spacing
        if spacing is not None:
            vz, vy, vx = spacing
        else:
            vz = vy = vx = settings.SIMULATION_VOXEL_SIZE
        radii = (
            config.get("radius_z", 10) / vz,
            config.get("radius_y", 10) / vy,
            config.get("radius_x", 10) / vx,
        )

        hu_mean = config.get("hu_mean", hu_defaults["mean"])
        hu_std = config.get("hu_std", hu_defaults["std"])

        return self._generate_lesion_volume(
            shape=volume_shape,
            center=center,
            radii=radii,
            hu_mean=hu_mean,
            hu_std=hu_std,
            margin_sharpness=config.get("margin_sharpness", 0.8),
            shape_type=config.get("shape", "spherical"),
            spiculation=config.get("spiculation_degree", 0.0),
        )

    def _generate_lesion_volume(
        self,
        shape: Tuple[int, int, int],
        center: Tuple[float, float, float],
        radii: Tuple[float, float, float],
        hu_mean: float,
        hu_std: float,
        margin_sharpness: float,
        shape_type: str = "spherical",
        spiculation: float = 0.0,
    ) -> np.ndarray:
        """Generate the lesion voxel grid with appropriate shape and HU values."""
        z, y, x = np.indices(shape, dtype=float)
        cz, cy, cx = center
        rz, ry, rx = radii

        # Normalized distance from center
        distance = np.sqrt(
            ((z - cz) / rz) ** 2 +
            ((y - cy) / ry) ** 2 +
            ((x - cx) / rx) ** 2
        )

        # Apply shape modifications
        if shape_type == "lobulated":
            distance = self._apply_lobulation(distance, z, y, x, cz, cy, cx, rz, ry, rx)
        elif shape_type == "spiculated":
            distance = self._apply_spiculation(distance, z, y, x, cz, cy, cx, rz, ry, rx, spiculation)
        elif shape_type == "irregular":
            distance = self._apply_irregularity(distance, z, y, x, cz, cy, cx)

        # Soft margin using sigmoid
        margin_width = 1.0 - margin_sharpness
        if margin_width > 0.01:
            mask = 1.0 / (1.0 + np.exp((distance - 1.0) / (margin_width * 0.2)))
        else:
            mask = (distance <= 1.0).astype(float)

        # Generate HU values with heterogeneity
        hu_volume = np.zeros(shape, dtype=np.float32)
        lesion_voxels = mask > 0.01

        # Base HU with noise
        hu_values = self.rng.normal(hu_mean, hu_std, shape)
        hu_volume[lesion_voxels] = hu_values[lesion_voxels]

        # Apply smooth transition at margins
        hu_volume = hu_volume * mask

        # Add internal structure (optional calcification/necrosis)
        # TODO: Add calcification and necrosis patterns

        return hu_volume

    def _apply_lobulation(
        self,
        distance: np.ndarray,
        z: np.ndarray, y: np.ndarray, x: np.ndarray,
        cz: float, cy: float, cx: float,
        rz: float, ry: float, rx: float,
    ) -> np.ndarray:
        """Add lobulated contours to the lesion shape."""
        # Multiple overlapping sinusoidal deformations
        for i in range(3):
            angle = self.rng.uniform(0, 2 * np.pi, 3)
            freq = self.rng.uniform(2, 4, 3)
            amp = self.rng.uniform(0.1, 0.3)
            deformation = amp * (
                np.sin(freq[0] * (z - cz) / rz + angle[0]) *
                np.sin(freq[1] * (y - cy) / ry + angle[1]) *
                np.sin(freq[2] * (x - cx) / rx + angle[2])
            )
            distance = distance / (1.0 + deformation)
        return distance

    def _apply_spiculation(
        self,
        distance: np.ndarray,
        z: np.ndarray, y: np.ndarray, x: np.ndarray,
        cz: float, cy: float, cx: float,
        rz: float, ry: float, rx: float,
        spiculation_degree: float,
    ) -> np.ndarray:
        """Add spiculated (spiky) margins to simulate malignancy."""
        # Radial spiculations
        theta = np.arctan2(y - cy, x - cx)
        phi = np.arctan2(z - cz, np.sqrt((x - cx) ** 2 + (y - cy) ** 2))

        n_spikes = 8 + int(spiculation_degree * 12)
        spike_angles = self.rng.uniform(0, 2 * np.pi, n_spikes)
        spike_amps = self.rng.uniform(0.2, 0.5, n_spikes) * spiculation_degree

        spiculation_field = np.zeros_like(distance)
        for sa, amp in zip(spike_angles, spike_amps):
            angular_dist = np.cos(theta - sa) * np.cos(phi)
            spiculation_field += amp * np.maximum(angular_dist, 0) ** 2

        return distance / (1.0 + spiculation_field * 3)

    def _apply_irregularity(
        self,
        distance: np.ndarray,
        z: np.ndarray, y: np.ndarray, x: np.ndarray,
        cz: float, cy: float, cx: float,
    ) -> np.ndarray:
        """Add Perlin-like irregular deformation to the lesion shape."""
        # Simplified irregularity using noise
        noise = self.rng.uniform(-0.2, 0.2, distance.shape)
        noise = gaussian_filter(noise, sigma=3)
        return distance * (1.0 + noise * 0.5)
