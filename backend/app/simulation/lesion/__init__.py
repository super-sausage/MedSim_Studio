"""Lesion simulation module for synthetic tumor/nodule generation.

Modes
-----
- Voxel (default): procedural ellipsoid with shape deformations
- Mesh:            load 3D mesh (STL/OBJ/VTK/PLY) via MeshGenerator
- Mask:            load NIfTI segmentation mask via MaskGenerator

Texture
-------
- Perlin / fractal noise via TextureGenerator (P1)
- Replaces simple Gaussian noise when texture_config is set

All modes produce the same output type: np.ndarray of HU values.
"""

from app.simulation.lesion.generator import LesionGenerator

# Mesh / mask generators are lazily imported inside generator.py
# to keep imports lightweight. Explicitly export here for direct use:
from app.simulation.lesion.mesh_generator import MeshGenerator, MaskGenerator
from app.simulation.lesion.texture_generator import TextureGenerator
from app.simulation.lesion.analyzer import analyze as analyze_lesion

__all__ = [
    "LesionGenerator",
    "MeshGenerator",
    "MaskGenerator",
    "TextureGenerator",
    "analyze_lesion",
]
