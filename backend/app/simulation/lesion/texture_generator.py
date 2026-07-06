"""
Texture Generator
==================

Replaces simple Gaussian noise with multi-scale realistic textures
for CT lesion simulation.

Provides:
  - 3D Perlin noise — smooth gradient noise with spatial correlation
  - Fractal noise — sum of Perlin octaves (1/f^β spectrum)
  - Multi-scale texture — combines coarse structure + fine detail
  - Lesion-type-specific texture presets

All functions are pure NumPy — no new dependencies.

Integration
-----------
This module is called from LesionGenerator._generate_lesion_volume()
to replace the single line:
    hu_values = self.rng.normal(hu_mean, hu_std, shape)
with:
    hu_values = TextureGenerator.generate_texture(shape, hu_mean, hu_std, config, rng)
"""

import logging
from typing import Dict, Any, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Perlin Noise — 3D pure NumPy implementation
# ---------------------------------------------------------------------------

def _generate_gradient_grid(
    grid_shape: Tuple[int, int, int],
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Generate random unit-gradient vectors on a regular grid.

    Returns:
        gradients: (gx, gy, gz, 3) array of random unit vectors
    """
    gx, gy, gz = grid_shape
    # Random angles
    theta = rng.uniform(0, 2 * np.pi, (gx, gy, gz, 1))  # azimuth
    phi = rng.uniform(0, np.pi, (gx, gy, gz, 1))  # polar

    # Convert to Cartesian unit vectors
    grads = np.concatenate(
        [
            np.sin(phi) * np.cos(theta),
            np.sin(phi) * np.sin(theta),
            np.cos(phi),
        ],
        axis=-1,
    )
    return grads.astype(np.float32)


def _fade(t: np.ndarray) -> np.ndarray:
    """6t^5 - 15t^4 + 10t^3 — smoothstep for Perlin interpolation."""
    return t * t * t * (t * (t * 6.0 - 15.0) + 10.0)


def _lerp(a: np.ndarray, b: np.ndarray, t: np.ndarray) -> np.ndarray:
    """Linear interpolation."""
    return a + t * (b - a)


def perlin_noise(
    shape: Tuple[int, int, int],
    scale: float = 16.0,
    seed: Optional[int] = None,
) -> np.ndarray:
    """
    3D Perlin noise via pure NumPy.

    Args:
        shape: Output noise shape (z, y, x)
        scale: Larger values → coarser features (in voxels).
               scale=16 means features roughly 16 voxels across.
        seed: Random seed for reproducibility

    Returns:
        noise array (float32) in range ~[-√0.5, √0.5]
    """
    rng = np.random.default_rng(seed)

    # Clamp scale: sub-voxel features are meaningless and cause memory blowup
    scale = max(scale, 1.0)

    # Determine grid resolution: at most 1 cell per voxel per axis
    grid = tuple(
        min(s, max(2, int(np.ceil(s / scale)) + 1)) for s in shape
    )
    gradients = _generate_gradient_grid(grid, rng)

    # Coordinate grid in [0, shape-1]
    z, y, x = np.indices(shape, dtype=np.float32)
    z = z / scale
    y = y / scale
    x = x / scale

    # Grid cell indices (integer part)
    z0 = np.floor(z).astype(np.int32)
    y0 = np.floor(y).astype(np.int32)
    x0 = np.floor(x).astype(np.int32)
    z1 = z0 + 1
    y1 = y0 + 1
    x1 = x0 + 1

    # Local coordinates in [0, 1]
    fz = z - z0.astype(np.float32)
    fy = y - y0.astype(np.float32)
    fx = x - x0.astype(np.float32)

    # Fade curves
    uz = _fade(fz)
    uy = _fade(fy)
    ux = _fade(fx)

    # Gradient lookup at each of 8 corners
    # Wrap grid coordinates to avoid out-of-bounds
    g = gradients
    gx_max, gy_max, gz_max = g.shape[0] - 1, g.shape[1] - 1, g.shape[2] - 1

    def _clamp_idx(arr, mx):
        return np.clip(arr, 0, mx)

    # Corner coordinates (clamped)
    z0c = _clamp_idx(z0, gz_max)
    z1c = _clamp_idx(z1, gz_max)
    y0c = _clamp_idx(y0, gy_max)
    y1c = _clamp_idx(y1, gy_max)
    x0c = _clamp_idx(x0, gx_max)
    x1c = _clamp_idx(x1, gx_max)

    # Dot products: gradient · (local offset)
    def _dot(cz, cy, cx, dz, dy, dx):
        return (
            g[cz, cy, cx, 0] * dz +
            g[cz, cy, cx, 1] * dy +
            g[cz, cy, cx, 2] * dx
        )

    n000 = _dot(z0c, y0c, x0c, fz, fy, fx)
    n100 = _dot(z0c, y0c, x1c, fz, fy, fx - 1)
    n010 = _dot(z0c, y1c, x0c, fz, fy - 1, fx)
    n110 = _dot(z0c, y1c, x1c, fz, fy - 1, fx - 1)
    n001 = _dot(z1c, y0c, x0c, fz - 1, fy, fx)
    n101 = _dot(z1c, y0c, x1c, fz - 1, fy, fx - 1)
    n011 = _dot(z1c, y1c, x0c, fz - 1, fy - 1, fx)
    n111 = _dot(z1c, y1c, x1c, fz - 1, fy - 1, fx - 1)

    # Trilinear interpolation
    nx00 = _lerp(n000, n100, ux)
    nx10 = _lerp(n010, n110, ux)
    nx01 = _lerp(n001, n101, ux)
    nx11 = _lerp(n011, n111, ux)
    nxy0 = _lerp(nx00, nx10, uy)
    nxy1 = _lerp(nx01, nx11, uy)
    noise = _lerp(nxy0, nxy1, uz)

    return noise.astype(np.float32)


# ---------------------------------------------------------------------------
# Fractal Noise
# ---------------------------------------------------------------------------


def fractal_noise(
    shape: Tuple[int, int, int],
    octaves: int = 4,
    persistence: float = 0.5,
    lacunarity: float = 2.0,
    base_scale: float = 32.0,
    seed: Optional[int] = None,
) -> np.ndarray:
    """
    Fractal (1/f^β) noise by summing Perlin octaves.

    Each octave doubles frequency and halves amplitude (by default),
    producing natural 1/f^β spectrum found in medical textures.

    Args:
        shape: Output shape (z, y, x)
        octaves: Number of summed octaves (more = finer detail)
        persistence: Amplitude scaling per octave (0.5 = half each step)
        lacunarity: Frequency scaling per octave (2.0 = double each step)
        base_scale: Scale of the coarsest octave (in voxels)
        seed: Random seed

    Returns:
        fractal noise (float32) in approximate range [-1, 1]
    """
    noise = np.zeros(shape, dtype=np.float32)
    amplitude = 1.0
    max_amplitude = 0.0
    scale = base_scale

    for octave in range(octaves):
        octave_seed = (seed or 0) + octave * 9973  # unique per octave
        layer = perlin_noise(shape, scale=scale, seed=octave_seed)
        noise += amplitude * layer
        max_amplitude += amplitude
        amplitude *= persistence
        scale /= lacunarity

    # Normalize to roughly [-1, 1]
    if max_amplitude > 0:
        noise /= max_amplitude

    return noise


# ---------------------------------------------------------------------------
# Texture Presets — lesion-type-specific texture parameters
# ---------------------------------------------------------------------------

TEXTURE_PRESETS: Dict[str, Dict[str, Any]] = {
    "tumor": {
        "description": "Soft tissue tumor with heterogeneous interior",
        "octaves": 4,
        "persistence": 0.5,
        "lacunarity": 2.0,
        "base_scale": 16.0,
        "coarse_weight": 0.6,
        "fine_weight": 0.4,
        "anisotropy": 1.0,
        "contrast": 1.0,
    },
    "nodule": {
        "description": "Pulmonary nodule — small, subtle texturing",
        "octaves": 3,
        "persistence": 0.4,
        "lacunarity": 2.5,
        "base_scale": 8.0,
        "coarse_weight": 0.4,
        "fine_weight": 0.6,
        "anisotropy": 1.2,
        "contrast": 0.7,
    },
    "cyst": {
        "description": "Fluid-filled cyst — nearly homogeneous",
        "octaves": 2,
        "persistence": 0.3,
        "lacunarity": 2.0,
        "base_scale": 24.0,
        "coarse_weight": 0.8,
        "fine_weight": 0.2,
        "anisotropy": 1.0,
        "contrast": 0.3,
    },
    "calcification": {
        "description": "Calcified lesion — sharp, high-density speckles",
        "octaves": 3,
        "persistence": 0.6,
        "lacunarity": 3.0,
        "base_scale": 8.0,
        "coarse_weight": 0.3,
        "fine_weight": 0.7,
        "anisotropy": 1.5,
        "contrast": 1.5,
    },
    "metastasis": {
        "description": "Metastatic lesion — irregular, heterogeneous",
        "octaves": 5,
        "persistence": 0.55,
        "lacunarity": 2.2,
        "base_scale": 20.0,
        "coarse_weight": 0.5,
        "fine_weight": 0.5,
        "anisotropy": 1.3,
        "contrast": 1.2,
    },
}


# ---------------------------------------------------------------------------
# Public API — used by LesionGenerator._generate_lesion_volume()
# ---------------------------------------------------------------------------


class TextureGenerator:
    """
    Multi-scale texture generator for CT lesions.

    Usage — called from LesionGenerator._generate_lesion_volume():
        hu_values = TextureGenerator.generate_texture(
            shape, hu_mean, hu_std, texture_config, rng,
        )
    Returns an array with the same spatial-correlation structure
    as natural tissue textures.
    """

    @staticmethod
    def generate_texture(
        shape: Tuple[int, int, int],
        hu_mean: float,
        hu_std: float,
        texture_config: Optional[Dict[str, Any]] = None,
        rng: Optional[np.random.Generator] = None,
    ) -> np.ndarray:
        """
        Generate HU values with multi-scale texture.

        Args:
            shape: Output volume shape (z, y, x)
            hu_mean: Target mean HU
            hu_std: Target HU standard deviation
            texture_config: Configuration dict. If None, falls back to
                            simple Gaussian noise (existing behaviour).
            rng: NumPy random generator

        Returns:
            HU values (float32) matching *shape* — same semantics as
            ``rng.normal(hu_mean, hu_std, shape)`` but with texture.
        """
        if texture_config is None:
            # Fallback: pure Gaussian noise (backward compatible)
            if rng is not None:
                return rng.normal(hu_mean, hu_std, shape).astype(np.float32)
            else:
                return np.random.default_rng().normal(
                    hu_mean, hu_std, shape
                ).astype(np.float32)

        rng = rng or np.random.default_rng()
        seed = int(rng.integers(0, 2**31))

        # Resolve preset or custom config
        lesion_type = texture_config.get("lesion_type", "tumor")
        preset = TEXTURE_PRESETS.get(lesion_type, TEXTURE_PRESETS["tumor"])

        octaves = texture_config.get("octaves", preset["octaves"])
        persistence = texture_config.get("persistence", preset["persistence"])
        lacunarity = texture_config.get("lacunarity", preset["lacunarity"])
        base_scale = texture_config.get("base_scale", preset["base_scale"])
        coarse_weight = texture_config.get("coarse_weight", preset["coarse_weight"])
        fine_weight = texture_config.get("fine_weight", preset["fine_weight"])
        contrast = texture_config.get("contrast", preset["contrast"])

        # 1. Coarse structure (low-frequency)
        coarse = fractal_noise(
            shape,
            octaves=octaves,
            persistence=persistence,
            lacunarity=lacunarity,
            base_scale=base_scale,
            seed=seed,
        )

        # 2. Fine detail (high-frequency, higher lacunarity, more octaves)
        fine = fractal_noise(
            shape,
            octaves=octaves + 1,
            persistence=persistence * 0.7,
            lacunarity=lacunarity * 1.5,
            base_scale=base_scale / 2.0,
            seed=seed + 1,
        )

        # 3. Blend coarse + fine
        combined = coarse_weight * coarse + fine_weight * fine

        # 4. Normalize to unit std so that hu_std * contrast scales correctly
        c_std = float(combined.std())
        if c_std > 1e-6:
            combined = combined / c_std

        # 5. Scale to target HU distribution
        combined = combined * hu_std * contrast + hu_mean

        logger.debug(
            "TextureGenerator: type=%s octaves=%d persistence=%.2f "
            "lacunarity=%.2f base_scale=%.1f contrast=%.2f "
            " → HU range [%.1f, %.1f]",
            lesion_type, octaves, persistence, lacunarity, base_scale,
            contrast,
            float(combined.min()), float(combined.max()),
        )

        return combined.astype(np.float32)

    @staticmethod
    def list_presets() -> Dict[str, Dict[str, Any]]:
        """Return available texture presets (read-only)."""
        return {
            k: {kk: vv for kk, vv in v.items()}
            for k, v in TEXTURE_PRESETS.items()
        }
