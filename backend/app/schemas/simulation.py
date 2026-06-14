"""
Simulation Pydantic Schemas

Request/response schemas for lesion and organ simulation APIs.
"""

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Optional, List


class LesionConfigCreate(BaseModel):
    """Schema for creating a lesion configuration."""
    lesion_type: str = Field(..., description="Type of lesion: tumor, nodule, cyst, calcification, metastasis")
    shape: str = Field("spherical", description="Geometric shape of the lesion")
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
