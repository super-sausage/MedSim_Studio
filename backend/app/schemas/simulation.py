"""
Simulation Pydantic Schemas

Request/response schemas for lesion, organ, and CT parameter simulation APIs.
"""

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field


class LesionConfigCreate(BaseModel):
    """Schema for creating a lesion configuration."""
    lesion_type: str = Field(..., description="Type of lesion: tumor, nodule, cyst, calcification, metastasis")
    shape: str = Field("spherical", description="Geometric shape of the lesion — used in voxel mode")
    center_x: float = Field(0.0, description="X coordinate in voxel space")
    center_y: float = Field(0.0, description="Y coordinate in voxel space")
    center_z: float = Field(0.0, description="Z coordinate in voxel space")
    radius_x: float = Field(10.0, description="Radius in X dimension (mm)")
    radius_y: float = Field(10.0, description="Radius in Y dimension (mm)")
    radius_z: float = Field(10.0, description="Radius in Z dimension (mm)")
    hu_mean: float = Field(40.0, description="Mean Hounsfield Unit value")
    hu_std: float = Field(20.0, description="HU standard deviation for heterogeneity")
    margin_sharpness: float = Field(0.8, ge=0, le=1, description="Margin sharpness (0=diffuse, 1=sharp)")
    calcification_fraction: float = Field(0.0, ge=0, le=1, description="Internal calcification fraction")
    necrosis_fraction: float = Field(0.0, ge=0, le=1, description="Internal necrosis fraction")
    spiculation_degree: float = Field(0.0, ge=0, le=1, description="Spiculation degree for malignant appearance")

    # NEW P0: Mesh / mask template support (mutually exclusive, None=voxel mode)
    mesh_path: Optional[str] = Field(
        None, description="Path to mesh file (.stl/.obj/.vtk/.ply) — enables mesh mode"
    )
    mask_path: Optional[str] = Field(
        None, description="Path to NIfTI mask file (.nii/.nii.gz) — enables mask mode"
    )
    mask_format: str = Field("nifti", description="Mask file format (currently only 'nifti')")

    # NEW P1: Texture generation
    texture_config: Optional[Dict[str, Any]] = Field(
        None, description=(
            "Texture parameters for multi-scale Perlin/fractal noise. "
            "Keys: octaves (int), persistence (float), lacunarity (float), "
            "base_scale (float), contrast (float). "
            "Set to {'enabled': True} for lesion-type-specific defaults. "
            "None = legacy Gaussian noise (backward compatible)."
        )
    )

    # NEW P2: Organ-aware lesion placement
    organ_constraint: Optional[str] = Field(
        None, description=(
            "Organ type for automatic placement (e.g. 'liver', 'kidney', 'lung'). "
            "When set and center_x/y/z are all 0 (default), the position is "
            "automatically chosen inside the specified organ. "
            "Requires OrganSimulator — uses synthetic organ geometry."
        )
    )


class LesionConfigResponse(LesionConfigCreate):
    """Response schema for a lesion configuration."""
    id: str
    job_id: str

    class Config:
        from_attributes = True


class SimulationJobCreate(BaseModel):
    """Schema for creating a simulation job."""
    study_id: Optional[str] = None
    series_id: Optional[str] = None
    lesions: List[LesionConfigCreate] = []
    organs: List[dict] = []
    output_format: str = Field("dicom", description="Output format: dicom, nifti, or nrrd")


class SimulationJobResponse(BaseModel):
    """Response schema for a simulation job."""
    id: str
    study_id: Optional[str] = None
    series_id: Optional[str] = None
    status: str = "pending"
    lesion_count: int = 0
    organ_count: int = 0
    progress: float = 0.0
    output_format: str = "dicom"
    output_path: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    lesions: List[LesionConfigResponse] = []

    class Config:
        from_attributes = True


class SimulationPreviewResponse(BaseModel):
    """Response schema for a simulation preview (synchronous)."""
    job_id: str
    preview_data: dict
    voxel_count: int
    hu_range: tuple


class DicomLesionPreviewRequest(BaseModel):
    """Request to preview a lesion on a real DICOM series."""
    series_id: str
    scan_direction: Literal["head_to_feet", "feet_to_head"] = "head_to_feet"
    lesion: LesionConfigCreate
    window_center: float = 40
    window_width: float = 400


class DicomLesionPreviewResponse(BaseModel):
    """Response with base64-encoded preview image and HU stats."""
    image_base64: str
    slice_index: int
    total_slices: int
    lesion_center_voxel: list
    hu_min: float
    hu_max: float
    hu_mean: float
    hu_std: float
    voxel_count: int
    volume_mm3: float


# ---------------------------------------------------------------------------
# Debug schemas
# ---------------------------------------------------------------------------


class DebugLesionRequest(BaseModel):
    """Request for lesion simulation debug endpoint."""
    lesion_type: str = Field("tumor", description="Type of lesion")
    shape: str = Field("spherical", description="Geometric shape")
    center_x: float = Field(0.0, description="Center X (voxel, 0=auto-center)")
    center_y: float = Field(0.0, description="Center Y (voxel, 0=auto-center)")
    center_z: float = Field(0.0, description="Center Z (voxel, 0=auto-center)")
    radius_x: float = Field(10.0, description="Radius X (mm)")
    radius_y: float = Field(10.0, description="Radius Y (mm)")
    radius_z: float = Field(10.0, description="Radius Z (mm)")
    hu_mean: float = Field(40.0, description="Mean HU")
    hu_std: float = Field(20.0, description="HU standard deviation")
    margin_sharpness: float = Field(0.8, ge=0, le=1)
    spiculation_degree: float = Field(0.0, ge=0, le=1)
    volume_shape: Optional[List[int]] = Field(
        None, description="Volume shape [z, y, x]. Defaults to [64, 128, 128] if unset"
    )
    spacing: Optional[List[float]] = Field(
        None, description="Voxel spacing [z, y, x] in mm. Defaults to [1.0, 0.5, 0.5]"
    )


class DebugLesionResponse(BaseModel):
    """Comprehensive debug response for lesion simulation."""

    # Task 1: Generation stats
    lesion_voxels: int = 0
    lesion_ratio: float = 0.0
    lesion_hu_mean: float = 0.0
    lesion_hu_min: float = 0.0
    lesion_hu_max: float = 0.0
    lesion_hu_std: float = 0.0

    # Task 2: Write stats
    changed_voxels: int = 0
    write_delta_mean: float = 0.0
    write_delta_max: float = 0.0

    # Task 3: Position
    volume_shape: List[int] = []
    center_voxel: List[float] = []
    bbox: dict = {}
    inside_volume: bool = False

    # Task 4: Spacing
    spacing: List[float] = []
    radius_mm: List[float] = []
    radius_voxel: List[float] = []
    z_compression_warning: bool = False

    # Preview image
    preview_png_base64: Optional[str] = None


# ---------------------------------------------------------------------------
# P3: Lesion Analysis schemas
# ---------------------------------------------------------------------------


class DiametersMM(BaseModel):
    """Lesion diameters in each axis."""
    z: float
    y: float
    x: float


class BBox(BaseModel):
    """Bounding box in voxel coordinates."""
    z_min: int
    z_max: int
    y_min: int
    y_max: int
    x_min: int
    x_max: int


class LesionAnalysisRequest(BaseModel):
    """Request for lesion structure analysis.

    Accepts parameters matching DebugLesionRequest (for generate-then-analyze).
    For analyzing an existing lesion, pass ``volume_data_base64`` and
    ``mask_data_base64`` instead.
    """
    # Generation parameters (used when no data is provided)
    lesion_type: str = Field("tumor", description="Type of lesion")
    shape: str = Field("spherical", description="Geometric shape")
    center_x: float = Field(0.0, description="Center X (voxel, 0=auto-center)")
    center_y: float = Field(0.0, description="Center Y (voxel, 0=auto-center)")
    center_z: float = Field(0.0, description="Center Z (voxel, 0=auto-center)")
    radius_x: float = Field(10.0, description="Radius X (mm)")
    radius_y: float = Field(10.0, description="Radius Y (mm)")
    radius_z: float = Field(10.0, description="Radius Z (mm)")
    hu_mean: float = Field(40.0, description="Mean HU")
    hu_std: float = Field(20.0, description="HU standard deviation")
    margin_sharpness: float = Field(0.8, ge=0, le=1)
    spiculation_degree: float = Field(0.0, ge=0, le=1)
    volume_shape: Optional[List[int]] = Field(
        None, description="Volume shape [z, y, x]. Defaults to [64, 128, 128]"
    )
    spacing: Optional[List[float]] = Field(
        None, description="Voxel spacing [z, y, x] in mm. Defaults to [1.0, 0.5, 0.5]"
    )

    # Analyze existing data (mutually exclusive with generation params)
    volume_data_base64: Optional[str] = Field(
        None, description="Base64-encoded raw float32 bytes of the CT volume (z,y,x)"
    )
    mask_data_base64: Optional[str] = Field(
        None, description="Base64-encoded raw uint8 bytes of the lesion mask (z,y,x)"
    )


class LesionAnalysisResponse(BaseModel):
    """Structured morphological and density analysis of a lesion."""
    voxel_count: int = 0
    volume_mm3: float = 0.0
    max_diameter_mm: float = 0.0
    diameters_mm: DiametersMM = Field(default_factory=lambda: DiametersMM(z=0, y=0, x=0))
    hu_mean: float = 0.0
    hu_std: float = 0.0
    hu_min: float = 0.0
    hu_max: float = 0.0
    surface_area_mm2: float = 0.0
    sphericity: float = 0.0
    bbox: BBox = Field(default_factory=lambda: BBox(z_min=0, z_max=0, y_min=0, y_max=0, x_min=0, x_max=0))
    shape_info: str = "empty"


class CTParamsPreviewParams(BaseModel):
    """CT scan parameter simulation controls for preview."""

    gantry_pitch_deg: float = Field(0.0, ge=-30.0, le=30.0)
    gantry_yaw_deg: float = Field(0.0, ge=-30.0, le=30.0)
    gantry_roll_deg: float = Field(0.0, ge=-30.0, le=30.0)
    slice_thickness_mm: Literal[0.625, 1.0, 2.5, 5.0, 10.0] = 1.0
    dose_level: Literal["low", "standard", "high"] = "standard"
    mAs: int = Field(150, ge=30, le=300)
    kVp: Literal[80, 100, 120, 140] = 120
    pitch: Literal[0.5, 0.8, 1.0, 1.2, 1.5] = 1.0
    fov_mm: Literal[150, 250, 350, 500] = 350
    matrix_size: Literal[256, 512, 1024] = 512
    kernel: Literal["soft", "standard", "lung", "bone", "sharp", "smooth"] = "standard"
    contrast_phase: Literal["noncontrast", "arterial", "venous", "delayed"] = "noncontrast"


class CTParamsPreviewRequest(BaseModel):
    """Request payload for CT parameter preview."""

    source: str = "atlas"
    case_id: Optional[str] = "LUNG1-001"
    study_id: Optional[str] = None
    series_id: Optional[str] = None
    size: int = Field(160, ge=64, le=320)
    scan_direction: Literal["head_to_feet", "feet_to_head"] = "head_to_feet"
    params: CTParamsPreviewParams


class CTParamsPreviewResponse(BaseModel):
    """Preview response for CT scan parameter simulation."""

    simulated_volume_base64: str
    metadata: Dict[str, Any]
    params_json: Dict[str, Any]
    standardized_case: Dict[str, Any]


# ---------------------------------------------------------------------------
# 3D Lesion Mesh Preview schemas — Phase 4/5
# ---------------------------------------------------------------------------


class MeshBounds(BaseModel):
    """Axis-aligned bounding box in physical (x, y, z) mm."""
    min: List[float] = Field(..., min_length=3, max_length=3)
    max: List[float] = Field(..., min_length=3, max_length=3)


class Lesion3DPreviewRequest(BaseModel):
    """Request for 3D mesh preview of a lesion configuration.

    Mirrors LesionConfigCreate / DebugLesionRequest fields so the frontend
    can send the same lesion parameters used in the 2D preview.
    """
    lesion_type: str = Field("tumor", description="Type of lesion")
    shape: str = Field("spherical", description="Geometric shape")
    center_x: float = Field(0.0, description="Center X (voxel, 0=auto-center)")
    center_y: float = Field(0.0, description="Center Y (voxel, 0=auto-center)")
    center_z: float = Field(0.0, description="Center Z (voxel, 0=auto-center)")
    radius_x: float = Field(10.0, description="Radius X (mm)")
    radius_y: float = Field(10.0, description="Radius Y (mm)")
    radius_z: float = Field(10.0, description="Radius Z (mm)")
    hu_mean: float = Field(40.0, description="Mean HU")
    hu_std: float = Field(20.0, description="HU standard deviation")
    margin_sharpness: float = Field(0.8, ge=0, le=1)
    spiculation_degree: float = Field(0.0, ge=0, le=1)

    # Mesh / mask mode
    mesh_path: Optional[str] = Field(
        None, description="Path to mesh file — enables mesh mode"
    )
    mask_path: Optional[str] = Field(
        None, description="Path to NIfTI mask — enables mask mode"
    )

    # Preview volume control
    preview_size: int = Field(
        64, ge=32, le=256,
        description="Preview volume edge size in voxels (isotropic)"
    )
    spacing: Optional[List[float]] = Field(
        None, description="Voxel spacing [z, y, x] in mm. Defaults to [1.0, 1.0, 1.0]"
    )


# ---------------------------------------------------------------------------
# Lesion-in-Phantom 3D Preview schemas
# ---------------------------------------------------------------------------


class LesionInPhantomPreviewRequest(BaseModel):
    """Request to preview a lesion embedded inside a CT phantom body.

    Generates a procedural CT phantom (upper-body anatomy), places the
    lesion inside (auto-placed in the right lung if center is not specified),
    and returns both the phantom volume and the lesion mesh.

    Center coordinates can be provided in two ways (mutually exclusive):
      1. normalized_center (recommended): 0-1 range, the backend scales them
         to its own phantom dimensions.
      2. center_x/y/z: raw voxel coordinates. Only use when the caller knows
         the exact phantom dimensions being generated.
    """
    lesion_type: str = Field("tumor", description="Type of lesion")
    shape: str = Field("spherical", description="Geometric shape")
    radius_x: float = Field(15.0, description="Radius X (mm)")
    radius_y: float = Field(15.0, description="Radius Y (mm)")
    radius_z: float = Field(15.0, description="Radius Z (mm)")
    hu_mean: float = Field(40.0, description="Mean HU")
    hu_std: float = Field(20.0, description="HU standard deviation")
    margin_sharpness: float = Field(0.8, ge=0, le=1)
    spiculation_degree: float = Field(0.0, ge=0, le=1)
    phantom_size: int = Field(160, ge=64, le=320, description="Phantom cube edge size in voxels")
    center_x: float = Field(0.0, description="Center X in phantom voxel space (0=auto)")
    center_y: float = Field(0.0, description="Center Y in phantom voxel space (0=auto)")
    center_z: float = Field(0.0, description="Center Z in phantom voxel space (0=auto)")
    normalized_center_x: float = Field(0.0, description="Normalized center X (0-1, overrides center_x if > 0)")
    normalized_center_y: float = Field(0.0, description="Normalized center Y (0-1, overrides center_y if > 0)")
    normalized_center_z: float = Field(0.0, description="Normalized center Z (0-1, overrides center_z if > 0)")


class LesionInPhantomPreviewResponse(BaseModel):
    """Response with CT phantom volume data + lesion triangle mesh.

    The phantom volume is returned as base64-encoded raw Float32 bytes
    in (z, y, x) axis order (x fastest). The lesion mesh vertices are
    in physical mm (x, y, z) and are already offset so they align with
    the VTK volume origin used by VolumeRenderer (centered volume).

    The frontend can pass the volume directly to VolumeRenderer as
    ``syntheticData`` with ``mode='synthetic'`` and overlay the mesh
    via ``lesionMeshes``.
    """
    phantom_volume_base64: str = Field(
        ..., description="Base64-encoded raw Float32 volume data (z, y, x, little-endian)"
    )
    phantom_shape: List[int] = Field(
        ..., min_length=3, max_length=3, description="Volume shape [z, y, x] in voxels"
    )
    phantom_spacing: List[float] = Field(
        ..., min_length=3, max_length=3, description="Voxel spacing [z, y, x] in mm"
    )
    lesion_vertices: List[List[float]] = Field(
        ..., description="N×3 vertex positions in physical (x, y, z) mm, in VTK centered space"
    )
    lesion_faces: List[List[int]] = Field(
        ..., description="M×3 triangle indices into vertices"
    )
    lesion_normals: List[List[float]] = Field(
        ..., description="N×3 per-vertex normal vectors"
    )
    lesion_center_mm: List[float] = Field(
        ..., min_length=3, max_length=3, description="Center of lesion bounding box [cx, cy, cz] in mm"
    )
    lesion_volume_mm3: float = Field(0.0, description="Approximate enclosed volume in mm³")


# ---------------------------------------------------------------------------
# DICOM 3D Lesion Preview — lesion embedded in real DICOM volume
# ---------------------------------------------------------------------------


class DicomLesion3DPreviewRequest(BaseModel):
    """Request to preview a lesion embedded inside a real DICOM volume in 3D.

    The endpoint loads the DICOM series, downsamples to a manageable size,
    generates the lesion at the specified position, and returns both the
    modified volume and the lesion triangle mesh.
    """
    series_id: str = Field(..., description="DICOM series ID to load")
    lesion_type: str = Field("tumor", description="Type of lesion")
    shape: str = Field("spherical", description="Geometric shape")
    radius_x: float = Field(15.0, description="Radius X (mm)")
    radius_y: float = Field(15.0, description="Radius Y (mm)")
    radius_z: float = Field(15.0, description="Radius Z (mm)")
    hu_mean: float = Field(40.0, description="Mean HU")
    hu_std: float = Field(20.0, description="HU standard deviation")
    margin_sharpness: float = Field(0.8, ge=0, le=1)
    spiculation_degree: float = Field(0.0, ge=0, le=1)
    normalized_center_x: float = Field(0.0, description="Normalized center X (0-1, 0=auto)")
    normalized_center_y: float = Field(0.0, description="Normalized center Y (0-1, 0=auto)")
    normalized_center_z: float = Field(0.0, description="Normalized center Z (0-1, 0=auto)")
    scan_direction: Literal["head_to_feet", "feet_to_head"] = "head_to_feet"
    preview_size: int = Field(192, ge=64, le=320, description="Max edge size in voxels for preview downsampling")


class DicomLesion3DPreviewResponse(BaseModel):
    """Response with downsampled DICOM volume + embedded lesion triangle mesh.

    The volume is downsampled so its largest dimension is <= preview_size.
    The lesion mesh vertices are offset to align with the VTK centered volume
    origin so the frontend can render them directly on top of each other.
    """
    volume_base64: str = Field(
        ..., description="Base64-encoded raw Float32 volume data (z, y, x, little-endian)"
    )
    volume_shape: List[int] = Field(
        ..., min_length=3, max_length=3, description="Volume shape [z, y, x] in voxels"
    )
    volume_spacing: List[float] = Field(
        ..., min_length=3, max_length=3, description="Voxel spacing [z, y, x] in mm"
    )
    lesion_vertices: List[List[float]] = Field(
        ..., description="N×3 vertex positions in physical (x, y, z) mm, in VTK centered space"
    )
    lesion_faces: List[List[int]] = Field(
        ..., description="M×3 triangle indices into vertices"
    )
    lesion_normals: List[List[float]] = Field(
        ..., description="N×3 per-vertex normal vectors"
    )
    lesion_center_mm: List[float] = Field(
        ..., min_length=3, max_length=3, description="Center of lesion bounding box [cx, cy, cz] in mm"
    )
    lesion_volume_mm3: float = Field(0.0, description="Approximate enclosed volume in mm³")


class Lesion3DPreviewResponse(BaseModel):
    """3D triangle mesh of a lesion, ready for vtk.js rendering."""
    vertices: List[List[float]] = Field(
        ..., description="N×3 vertex positions in physical (x, y, z) mm"
    )
    faces: List[List[int]] = Field(
        ..., description="M×3 triangle indices into vertices"
    )
    normals: List[List[float]] = Field(
        ..., description="N×3 per-vertex normal vectors"
    )
    bounds: MeshBounds = Field(
        ..., description="Axis-aligned bounding box in physical mm"
    )
    center: List[float] = Field(
        ..., description="Center of bounding box [cx, cy, cz] in mm"
    )
    volume_mm3: float = Field(
        0.0, description="Approximate enclosed volume in mm³"
    )
