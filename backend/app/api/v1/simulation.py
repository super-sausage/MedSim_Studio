"""
Simulation API

RESTful endpoints for lesion and organ simulation management.
Provides job creation, status tracking, preview generation,
and result export for synthetic medical image generation.
"""

import os
import uuid
import tempfile
import logging
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database.session import get_db, SessionLocal
from app.models.simulation import SimulationJob, LesionConfig, OrganConfig
from app.models.dicom import DicomInstance
from app.schemas.simulation import (
    SimulationJobResponse,
    SimulationJobCreate,
    LesionConfigResponse,
    SimulationPreviewResponse,
)
from app.simulation.lesion.generator import LesionGenerator
from app.simulation.organ.simulator import OrganSimulator
from app.simulation.volume_builder import build_volume_from_dicom, build_synthetic_volume
from app.simulation.exporter import export_nrrd, export_nifti, export_dicom_zip
from app.dicom.storage import get_storage_backend

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/simulation", tags=["Simulation"])


# ---------------------------------------------------------------------------
# Background task: run simulation job
# ---------------------------------------------------------------------------


def run_simulation_job(job_id: str) -> None:
    """
    Execute a simulation job in the background.

    Creates its own database session (does NOT reuse the request session)
    to avoid cross-thread / cross-request session reuse issues.

    Status flow:
        pending -> running -> completed / failed

    Phase 2 implementation:
      1. Build base volume (from DICOM source or synthetic fallback)
      2. Apply LesionGenerator / OrganSimulator overlays
      3. Write result as NRRD via SimpleITK to a temp file
      4. Upload temp file to storage backend
      5. Set job.output_path = "simulation/{job_id}/result.nrrd"
      6. Clean up temp files

    output_path always holds a storage object_key (never a local path).
    """
    import SimpleITK as sitk
    import numpy as np

    db: Session = SessionLocal()
    temp_nrrd_path: Optional[str] = None

    try:
        job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
        if not job:
            logger.error("run_simulation_job: job %s not found, exiting", job_id)
            return

        # --- Transition: pending -> running ---
        job.status = "running"
        job.progress = 10.0
        job.started_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info("Job %s transitioned to running", job_id)

        # --- Step 1: Build base volume (progress -> 25) ---
        storage = get_storage_backend()
        volume: Optional[np.ndarray] = None
        metadata: Optional[dict] = None

        if job.series_id:
            # Try to read source DICOM from storage backend
            instances = (
                db.query(DicomInstance)
                .filter(DicomInstance.series_id == job.series_id)
                .order_by(DicomInstance.instance_number.asc().nulls_last())
                .all()
            )
            if instances:
                try:
                    volume, metadata = build_volume_from_dicom(storage, instances)
                    logger.info(
                        "Job %s: built volume from DICOM series %s (%d slices)",
                        job_id, job.series_id, len(instances),
                    )
                except Exception as e:
                    logger.warning(
                        "Job %s: failed to build volume from DICOM (%s), "
                        "falling back to synthetic",
                        job_id, e,
                    )
                    volume, metadata = None, None

        if volume is None:
            # No source DICOM or read failed — generate synthetic base volume
            volume, metadata = build_synthetic_volume()
            logger.info("Job %s: using synthetic base volume", job_id)

        job.progress = 25.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 2: Apply simulation overlays (progress -> 50) ---
        result_volume = volume.copy()

        # Apply lesions
        lesion_configs = (
            db.query(LesionConfig)
            .filter(LesionConfig.job_id == job_id)
            .all()
        )
        if lesion_configs:
            lesion_gen = LesionGenerator()
            for lc in lesion_configs:
                config_dict = {
                    "lesion_type": lc.lesion_type,
                    "shape": lc.shape,
                    "center_x": lc.center_x,
                    "center_y": lc.center_y,
                    "center_z": lc.center_z,
                    "radius_x": lc.radius_x,
                    "radius_y": lc.radius_y,
                    "radius_z": lc.radius_z,
                    "hu_mean": lc.hu_mean,
                    "hu_std": lc.hu_std,
                    "margin_sharpness": lc.margin_sharpness,
                    "calcification_fraction": lc.calcification_fraction,
                    "necrosis_fraction": lc.necrosis_fraction,
                    "spiculation_degree": lc.spiculation_degree,
                }
                lesion_vol = lesion_gen.generate_lesion(
                    volume_shape=result_volume.shape,
                    config=config_dict,
                )
                result_volume = result_volume + lesion_vol
                logger.info(
                    "Job %s: applied lesion %s (%s)",
                    job_id, lc.id, lc.lesion_type,
                )

        # Apply organs
        organ_configs = (
            db.query(OrganConfig)
            .filter(OrganConfig.job_id == job_id)
            .all()
        )
        if organ_configs:
            organ_sim = OrganSimulator()
            for oc in organ_configs:
                config_dict = {
                    "organ_type": oc.organ_type,
                    "hu_mean": oc.hu_mean,
                    "hu_std": oc.hu_std,
                    "enable_noise": oc.enable_noise,
                    "noise_level": oc.noise_level,
                    "enable_enhancement": oc.enable_enhancement,
                    "enhancement_pattern": oc.enhancement_pattern,
                }
                organ_vol = organ_sim.generate_organ(
                    volume_shape=result_volume.shape,
                    config=config_dict,
                )
                # Only add organ where result is still background (avoid overwriting lesions)
                organ_mask = organ_vol != 0
                result_volume[organ_mask] = organ_vol[organ_mask]
                logger.info(
                    "Job %s: applied organ %s (%s)",
                    job_id, oc.id, oc.organ_type,
                )

        job.progress = 50.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 3: Write NRRD to temp file (progress -> 75) ---
        spacing = metadata.get("spacing", (1.0, 1.0, 1.0))
        origin = metadata.get("origin", (0.0, 0.0, 0.0))
        direction = metadata.get("direction", (1, 0, 0, 0, 1, 0, 0, 0, 1))

        # SimpleITK expects spacing/origin in (x, y, z) order, but our
        # volume is (z, y, x). Reverse for SimpleITK compatibility.
        sitk_spacing = (spacing[2], spacing[1], spacing[0])
        sitk_origin = (origin[2], origin[1], origin[0])

        sitk_image = sitk.GetImageFromArray(result_volume.astype(np.float32))
        sitk_image.SetSpacing(sitk_spacing)
        sitk_image.SetOrigin(sitk_origin)
        if len(direction) == 9:
            sitk_image.SetDirection(direction)

        # Write to temp file
        temp_dir = tempfile.mkdtemp(prefix=f"sim_{job_id}_")
        temp_nrrd_path = os.path.join(temp_dir, "result.nrrd")
        sitk.WriteImage(sitk_image, temp_nrrd_path)
        logger.info(
            "Job %s: wrote temp NRRD %s (%.1f MB)",
            job_id, temp_nrrd_path,
            os.path.getsize(temp_nrrd_path) / (1024 * 1024),
        )

        job.progress = 75.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 4: Upload to storage backend (progress -> 90) ---
        object_key = f"simulation/{job_id}/result.nrrd"
        upload_ok = storage.upload_file(
            object_key=object_key,
            file_path=temp_nrrd_path,
            content_type="application/octet-stream",
        )
        if not upload_ok:
            raise RuntimeError(
                f"Failed to upload result NRRD to storage (key={object_key})"
            )
        logger.info("Job %s: uploaded result to storage key=%s", job_id, object_key)

        job.progress = 90.0
        job.updated_at = datetime.utcnow()
        db.commit()

        # --- Step 5: Mark completed with output_path (progress -> 100) ---
        job.output_path = object_key
        job.output_format = "nrrd"
        job.status = "completed"
        job.progress = 100.0
        job.completed_at = datetime.utcnow()
        job.updated_at = datetime.utcnow()
        db.commit()
        logger.info("Job %s completed: output_path=%s", job_id, object_key)

    except Exception as e:
        logger.exception("run_simulation_job: unhandled error for job %s", job_id)
        try:
            db.rollback()
            job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
            if job:
                job.status = "failed"
                job.error_message = f"{type(e).__name__}: {str(e)[:200]}"
                job.updated_at = datetime.utcnow()
                # output_path stays None on failure — never write a fake value
                db.commit()
                logger.info("Job %s marked as failed", job_id)
        except Exception:
            logger.exception(
                "run_simulation_job: failed to update job %s to failed state",
                job_id,
            )
            try:
                db.rollback()
            except Exception:
                pass
    finally:
        # Clean up temp files
        if temp_nrrd_path:
            try:
                temp_dir = os.path.dirname(temp_nrrd_path)
                if os.path.isdir(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir, ignore_errors=True)
                    logger.info("Job %s: cleaned up temp dir %s", job_id, temp_dir)
            except Exception:
                logger.warning("Job %s: failed to clean temp dir", job_id)

        db.close()


# ---------------------------------------------------------------------------
# CRUD endpoints
# ---------------------------------------------------------------------------


@router.post("/jobs", response_model=SimulationJobResponse, status_code=status.HTTP_201_CREATED)
async def create_simulation_job(
    config: SimulationJobCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """
    Create a new simulation job and enqueue it for background execution.

    The job starts with status='pending'. After the HTTP response is sent,
    FastAPI triggers run_simulation_job() in the background, which
    transitions the job through running -> completed/failed.
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
        updated_at=datetime.utcnow(),
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

    # Enqueue background execution — job_id is a plain string,
    # the background task creates its own DB session.
    background_tasks.add_task(run_simulation_job, job_id)

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
    job.updated_at = datetime.utcnow()
    db.commit()
    return {"status": "cancelled", "job_id": job_id}


# ---------------------------------------------------------------------------
# Preview
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Export (placeholder — Phase 3 will implement real logic)
# ---------------------------------------------------------------------------


@router.get("/jobs/{job_id}/export")
async def export_simulation_results(
    job_id: str,
    format: str = Query("dicom", description="Export format: dicom, nifti, nrrd"),
    db: Session = Depends(get_db),
):
    """
    Export simulation results in the specified format.

    Streams downloadable DICOM (zip), NIfTI (.nii.gz), or NRRD files
    containing the simulated lesions and organs.

    Returns:
        StreamingResponse with Content-Disposition attachment
    """
    job = db.query(SimulationJob).filter(SimulationJob.id == job_id).first()
    if not job:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Simulation job {job_id} not found",
        )

    if job.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not completed (current status: {job.status})",
        )

    if not job.output_path:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Job output_path is empty — simulation did not complete successfully",
        )

    allowed_formats = ("dicom", "nifti", "nrrd")
    if format not in allowed_formats:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid format '{format}'. Allowed: {', '.join(allowed_formats)}",
        )

    storage = get_storage_backend()

    try:
        if format == "nrrd":
            return export_nrrd(job, storage)
        elif format == "nifti":
            return export_nifti(job, storage)
        elif format == "dicom":
            return export_dicom_zip(job, storage)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("Export failed for job %s, format %s", job_id, format)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Export failed: {str(e)[:200]}",
        )
