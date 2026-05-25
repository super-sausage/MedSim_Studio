"""
Simulation API

RESTful endpoints for lesion and organ simulation management.
Provides job creation, status tracking, preview generation,
and result export for synthetic medical image generation.
"""

import uuid
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.simulation import SimulationJob, LesionConfig
from app.schemas.simulation import (
    SimulationJobResponse,
    SimulationJobCreate,
    LesionConfigResponse,
    SimulationPreviewResponse,
)
from app.simulation.lesion.generator import LesionGenerator

router = APIRouter(prefix="/simulation", tags=["Simulation"])


@router.post("/jobs", response_model=SimulationJobResponse, status_code=status.HTTP_201_CREATED)
async def create_simulation_job(
    config: SimulationJobCreate,
    db: Session = Depends(get_db),
):
    """
    Create a new simulation job.

    Configures lesion and organ parameters for synthetic data generation.
    Returns the job configuration that can be used to track progress.
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
    db.commit()
    return {"status": "cancelled", "job_id": job_id}


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


@router.get("/jobs/{job_id}/export")
async def export_simulation_results(
    job_id: str,
    format: str = Query("dicom", description="Export format: dicom, nifti, nrrd"),
    db: Session = Depends(get_db),
):
    """
    Export simulation results in the specified format.

    Generates downloadable DICOM, NIfTI, or NRRD files
    containing the simulated lesions and organs.
    """
    job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation job {job_id} not found",
        )
    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Job is not completed (status: {job.status})",
        )
    # TODO: Implement actual export logic
    return {
        "job_id": job_id,
        "format": format,
        "message": "Export initiated",
        "download_url": f"/api/v1/simulation/jobs/{job_id}/download",
    }
