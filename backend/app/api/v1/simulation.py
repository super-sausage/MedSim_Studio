"""
Simulation API

RESTful endpoints for lesion and organ simulation management.
Provides job creation, status tracking, preview generation,
CT phantom generation, and result export for synthetic medical image generation.
"""

import os
import io
import base64
import uuid
import tempfile
import logging
from typing import List, Optional, Tuple
from datetime import datetime
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session
from PIL import Image

import numpy as np

from app.database.session import get_db, SessionLocal
from app.models.simulation import SimulationJob, LesionConfig, OrganConfig
from app.models.dicom import DicomInstance
from app.schemas.simulation import (
    SimulationJobResponse,
    SimulationJobCreate,
    LesionConfigResponse,
    SimulationPreviewResponse,
    DicomLesionPreviewRequest,
    DicomLesionPreviewResponse,
    DebugLesionRequest,
    DebugLesionResponse,
)
from app.simulation.lesion.generator import LesionGenerator
from app.simulation.organ.simulator import OrganSimulator
from app.simulation.volume_builder import build_volume_from_dicom, build_synthetic_volume
from app.simulation.exporter import export_nrrd, export_nifti, export_dicom_zip
from app.simulation.phantom_generator import (
    generate_upper_body_ct_phantom,
    generate_atlas_ct_phantom,
)
from app.dicom.storage import get_storage_backend

# ── Debug output directory ──
DEBUG_OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))),
    "debug_output",
)

# Optional matplotlib for debug PNG generation
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ── Debug visualization helpers ──


def _debug_save_lesion_pngs(
    base_volume: np.ndarray,
    result_volume: np.ndarray,
    lesion_volume: np.ndarray,
    lesion_mask: np.ndarray,
    label: str,
    output_dir: str = DEBUG_OUTPUT_DIR,
) -> None:
    """
    Save 4 debug PNG slices through the lesion center (axial middle slice).

    Files saved:
        lesion_mask_middle_slice.png
        lesion_hu_middle_slice.png
        result_volume_middle_slice.png
        difference_map.png
    """
    if not HAS_MPL:
        logger.warning("matplotlib not installed — skipping debug PNGs")
        return

    os.makedirs(output_dir, exist_ok=True)

    # Find the axial slice at the lesion center
    nonzero = np.argwhere(lesion_mask)
    if len(nonzero) == 0:
        logger.warning("_debug_save_lesion_pngs: lesion_mask is empty, nothing to visualize")
        return
    cz = int(np.median(nonzero[:, 0]))
    cy = int(np.median(nonzero[:, 1]))
    cx = int(np.median(nonzero[:, 2]))

    diff_map = np.abs(result_volume.astype(np.float32) - base_volume.astype(np.float32))

    figures = [
        ("lesion_mask_middle_slice.png", lesion_mask[cz, :, :].astype(np.uint8) * 255,
         "Lesion Mask (axial z={})".format(cz), "gray"),
        ("lesion_hu_middle_slice.png", lesion_volume[cz, :, :],
         "Lesion HU (axial z={})".format(cz), "viridis"),
        ("result_volume_middle_slice.png", result_volume[cz, :, :],
         "Result Volume HU (axial z={})".format(cz), "gray"),
        ("difference_map.png", diff_map[cz, :, :],
         "|Δ HU| (axial z={})".format(cz), "hot"),
    ]

    for fname, data, title, cmap in figures:
        fig, ax = plt.subplots(1, 1, figsize=(8, 6))
        im = ax.imshow(data, cmap=cmap, aspect="equal")
        ax.set_title(title)
        ax.set_xlabel("x")
        ax.set_ylabel("y")
        plt.colorbar(im, ax=ax, shrink=0.75)
        path = os.path.join(output_dir, f"{label}_{fname}")
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        logger.info("DEBUG PNG saved: %s", path)


def _debug_log_lesion_write(
    base_volume: np.ndarray,
    result_volume: np.ndarray,
    label: str,
) -> None:
    """Log before/after stats for lesion write step."""
    before = base_volume.astype(np.float32)
    after = result_volume.astype(np.float32)
    delta = after - before
    changed = np.count_nonzero(delta)

    logger.debug(
        "==== LESION WRITE DEBUG [%s] ====\n"
        "  before_mean:  %.2f  before_std:  %.2f\n"
        "  after_mean:   %.2f  after_std:   %.2f\n"
        "  delta_mean:   %.6f  delta_max:   %.2f  delta_min:   %.2f\n"
        "  changed_voxels: %d  (total: %d, ratio: %.6f)",
        label,
        float(np.mean(before)), float(np.std(before)),
        float(np.mean(after)), float(np.std(after)),
        float(np.mean(delta)), float(np.max(delta)), float(np.min(delta)),
        changed, before.size, changed / max(before.size, 1),
    )
    if changed == 0:
        logger.warning("LESION WRITE: changed_voxels == 0 — lesion did NOT modify the volume!")


def _debug_log_position(
    lesion_mask: np.ndarray,
    center: Tuple[float, float, float],
    volume_shape: Tuple[int, int, int],
    label: str,
) -> None:
    """Log position validation for a lesion."""
    nonzero = np.argwhere(lesion_mask)
    if len(nonzero) == 0:
        logger.warning("==== LESION POSITION DEBUG [%s] ==== mask is empty — no position to validate", label)
        return

    bbox = {
        "z_min": int(nonzero[:, 0].min()),
        "z_max": int(nonzero[:, 0].max()),
        "y_min": int(nonzero[:, 1].min()),
        "y_max": int(nonzero[:, 1].max()),
        "x_min": int(nonzero[:, 2].min()),
        "x_max": int(nonzero[:, 2].max()),
    }
    inside = (
        bbox["z_min"] >= 0 and bbox["z_max"] < volume_shape[0]
        and bbox["y_min"] >= 0 and bbox["y_max"] < volume_shape[1]
        and bbox["x_min"] >= 0 and bbox["x_max"] < volume_shape[2]
    )

    logger.debug(
        "==== LESION POSITION DEBUG [%s] ====\n"
        "  volume_shape:  %s\n"
        "  center:        (%.1f, %.1f, %.1f)  (z, y, x)\n"
        "  bbox_z:        [%d, %d]  (size: %d)\n"
        "  bbox_y:        [%d, %d]  (size: %d)\n"
        "  bbox_x:        [%d, %d]  (size: %d)\n"
        "  inside_volume: %s",
        label,
        str(volume_shape),
        center[0], center[1], center[2],
        bbox["z_min"], bbox["z_max"], bbox["z_max"] - bbox["z_min"] + 1,
        bbox["y_min"], bbox["y_max"], bbox["y_max"] - bbox["y_min"] + 1,
        bbox["x_min"], bbox["x_max"], bbox["x_max"] - bbox["x_min"] + 1,
        "YES" if inside else "OUTSIDE",
    )
    if not inside:
        logger.warning("LESION POSITION: lesion is OUTSIDE the volume!")


def _debug_verify_sitk_metadata(
    sitk_image: "sitk.Image",
    nrrd_path: str,
    label: str,
) -> None:
    """
    Verify that SimpleITK preserves origin/spacing/direction after writing.

    Writes the image, reads it back, and compares the metadata.
    This catches coordinate-system corruption during the write round-trip.
    """
    import SimpleITK as _sitk

    # Capture what was SET before write
    orig_origin = sitk_image.GetOrigin()
    orig_spacing = sitk_image.GetSpacing()
    orig_direction = sitk_image.GetDirection()

    # Read back
    try:
        reread = _sitk.ReadImage(nrrd_path)
    except Exception as e:
        logger.error("==== SITK META DEBUG [%s] ==== FAILED to read back: %s", label, e)
        return

    rb_origin = reread.GetOrigin()
    rb_spacing = reread.GetSpacing()
    rb_direction = reread.GetDirection()

    origin_ok = orig_origin == rb_origin
    spacing_ok = orig_spacing == rb_spacing
    direction_ok = orig_direction == rb_direction

    logger.debug(
        "==== SITK META DEBUG [%s] ====\n"
        "  --- Set ---                    --- Read back ---              Match?\n"
        "  Origin:     (%6.2f, %6.2f, %6.2f)    (%6.2f, %6.2f, %6.2f)   %s\n"
        "  Spacing:    (%6.4f, %6.4f, %6.4f)    (%6.4f, %6.4f, %6.4f)   %s\n"
        "  Direction:  (%s)  (%s)  %s",
        label,
        orig_origin[0], orig_origin[1], orig_origin[2],
        rb_origin[0], rb_origin[1], rb_origin[2],
        "OK" if origin_ok else "MISMATCH",
        orig_spacing[0], orig_spacing[1], orig_spacing[2],
        rb_spacing[0], rb_spacing[1], rb_spacing[2],
        "OK" if spacing_ok else "MISMATCH",
        ",".join(f"{v:.4f}" for v in orig_direction),
        ",".join(f"{v:.4f}" for v in rb_direction),
        "OK" if direction_ok else "MISMATCH",
    )

    if not origin_ok:
        logger.warning("SITK META: Origin MISMATCH — coordinate system may be corrupted!")
    if not spacing_ok:
        logger.warning("SITK META: Spacing MISMATCH — voxel dimensions changed!")
    if not direction_ok:
        logger.warning("SITK META: Direction MISMATCH — orientation may have flipped!")

    # Additional: log written file size
    file_size_mb = os.path.getsize(nrrd_path) / (1024 * 1024)
    logger.debug("  File size: %.2f MB", file_size_mb)


def _debug_log_spacing(
    spacing: Optional[Tuple[float, float, float]],
    radius_mm: Tuple[float, float, float],
    label: str,
) -> None:
    """Log spacing-to-voxel conversion diagnostics."""
    if spacing is None:
        logger.debug("==== SPACING DEBUG [%s] ==== spacing=None, using default (1,1,1)", label)
        spacing = (1.0, 1.0, 1.0)

    radius_voxel = (
        radius_mm[0] / spacing[0],  # z
        radius_mm[1] / spacing[1],  # y
        radius_mm[2] / spacing[2],  # x
    )

    logger.debug(
        "==== SPACING DEBUG [%s] ====\n"
        "  spacing (z,y,x):   (%.4f, %.4f, %.4f)\n"
        "  radius_mm (z,y,x): (%.1f, %.1f, %.1f)\n"
        "  radius_voxel:      (%.2f, %.2f, %.2f)\n"
        "  min_voxel_dim:     %.2f\n"
        "  Z compression:     %s (voxel count %.1f — if < 3, lesion is a 2D pancake)",
        label,
        spacing[0], spacing[1], spacing[2],
        radius_mm[0], radius_mm[1], radius_mm[2],
        radius_voxel[0], radius_voxel[1], radius_voxel[2],
        min(radius_voxel),
        "WARNING" if radius_voxel[0] < 3 else "OK",
        radius_voxel[0],
    )
    if radius_voxel[0] < 2:
        logger.warning("SPACING: Z-radius is only %.1f voxels — lesion will be a 2D pancake!", radius_voxel[0])
    if radius_voxel[1] < 2 or radius_voxel[2] < 2:
        logger.warning(
            "SPACING: in-plane radius is < 2 voxels (y=%.1f, x=%.1f) — lesion may be invisible!",
            radius_voxel[1], radius_voxel[2],
        )


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
            spacing = metadata.get("spacing")
            for lc in lesion_configs:
                _label = f"job_{job_id[:8]}_{lc.lesion_type}_{lc.id[:8]}"

                # Normalize center: if all zeros (frontend default), place at volume center
                cz, cy, cx = lc.center_z, lc.center_y, lc.center_x
                if cz == 0.0 and cy == 0.0 and cx == 0.0:
                    cz = float(result_volume.shape[0] // 2)
                    cy = float(result_volume.shape[1] // 2)
                    cx = float(result_volume.shape[2] // 2)
                config_dict = {
                    "lesion_type": lc.lesion_type,
                    "shape": lc.shape,
                    "center_x": cx,
                    "center_y": cy,
                    "center_z": cz,
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
                # ── DEBUG: spacing verification (Task 4) ──
                _debug_log_spacing(
                    spacing=spacing,
                    radius_mm=(lc.radius_z, lc.radius_y, lc.radius_x),
                    label=_label,
                )

                lesion_vol = lesion_gen.generate_lesion(
                    volume_shape=result_volume.shape,
                    config=config_dict,
                    spacing=spacing,
                )
                lesion_mask = lesion_vol != 0

                # ── DEBUG: position validation (Task 3) ──
                _debug_log_position(
                    lesion_mask=lesion_mask,
                    center=(cz, cy, cx),
                    volume_shape=result_volume.shape,
                    label=_label,
                )

                # ── DEBUG: before/after write stats (Task 2) ──
                _base_before = result_volume.copy()
                result_volume[lesion_mask] = lesion_vol[lesion_mask]
                _debug_log_lesion_write(
                    base_volume=_base_before,
                    result_volume=result_volume,
                    label=_label,
                )

                # ── DEBUG: save visualization PNGs (Task 5) ──
                _debug_save_lesion_pngs(
                    base_volume=volume,
                    result_volume=result_volume,
                    lesion_volume=lesion_vol,
                    lesion_mask=lesion_mask,
                    label=_label,
                )

                logger.info(
                    "Job %s: applied lesion %s (%s) — voxels=%d",
                    job_id, lc.id, lc.lesion_type, int(lesion_mask.sum()),
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

        # ── DEBUG: verify SimpleITK metadata round-trip (Task 6) ──
        _debug_verify_sitk_metadata(
            sitk_image=sitk_image,
            nrrd_path=temp_nrrd_path,
            label=f"job_{job_id[:8]}",
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


@router.post("/preview/lesion-on-dicom", response_model=DicomLesionPreviewResponse)
async def preview_lesion_on_dicom(
    request: DicomLesionPreviewRequest,
    db: Session = Depends(get_db),
):
    """
    Preview a lesion overlaid on a real DICOM series.

    Loads the DICOM series from storage, generates the lesion on it,
    and returns a base64-encoded side-by-side PNG (original | with lesion)
    of the axial slice through the lesion center.

    Synchronous — intended for interactive parameter tuning.
    """
    try:
        # ── 1. Load DICOM instances ──
        instances = (
            db.query(DicomInstance)
            .filter(DicomInstance.series_id == request.series_id)
            .order_by(DicomInstance.instance_number.asc().nulls_last())
            .all()
        )
        if not instances:
            raise HTTPException(status_code=404, detail=f"Series {request.series_id} not found")

        storage = get_storage_backend()
        volume, metadata = build_volume_from_dicom(storage, instances)

        # ── 2. Generate lesion ──
        generator = LesionGenerator()
        lesion_cfg = request.lesion

        # Normalize center: if all zeros (frontend default), place at volume center
        cz, cy, cx = lesion_cfg.center_z, lesion_cfg.center_y, lesion_cfg.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume.shape[0] // 2)
            cy = float(volume.shape[1] // 2)
            cx = float(volume.shape[2] // 2)

        config_dict = {
            "lesion_type": lesion_cfg.lesion_type,
            "shape": lesion_cfg.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": lesion_cfg.radius_x,
            "radius_y": lesion_cfg.radius_y,
            "radius_z": lesion_cfg.radius_z,
            "hu_mean": lesion_cfg.hu_mean,
            "hu_std": lesion_cfg.hu_std,
            "margin_sharpness": lesion_cfg.margin_sharpness,
            "calcification_fraction": lesion_cfg.calcification_fraction,
            "necrosis_fraction": lesion_cfg.necrosis_fraction,
            "spiculation_degree": lesion_cfg.spiculation_degree,
        }
        spacing = metadata.get("spacing")
        lesion_vol = generator.generate_lesion(
            volume_shape=volume.shape,
            config=config_dict,
            spacing=spacing,
        )
        lesion_mask = lesion_vol != 0
        result_volume = volume.copy()
        result_volume[lesion_mask] = lesion_vol[lesion_mask]

        # ── 3. Compute lesion region stats ──
        if lesion_mask.any():
            hu_values = result_volume[lesion_mask]
            stats_voxels = int(np.sum(lesion_mask))
            stats_hu_min = float(np.min(hu_values))
            stats_hu_max = float(np.max(hu_values))
            stats_hu_mean = float(np.mean(hu_values))
            stats_hu_std = float(np.std(hu_values))
            spacing = metadata.get("spacing", (1.0, 1.0, 1.0))
            voxel_vol_mm3 = spacing[0] * spacing[1] * spacing[2]
            stats_volume_mm3 = float(stats_voxels * voxel_vol_mm3)
        else:
            stats_voxels = 0
            stats_hu_min = stats_hu_max = stats_hu_mean = stats_hu_std = 0.0
            stats_volume_mm3 = 0.0

        # ── 4. Find the slice closest to the lesion center ──
        cz_idx = int(round(cz))
        cz_idx = max(0, min(cz_idx, volume.shape[0] - 1))

        # ── 5. Render side-by-side preview PNG ──
        wc = request.window_center
        ww = request.window_width
        half = ww / 2.0
        lower = wc - half
        upper = wc + half

        def slice_to_8bit(slice_2d: np.ndarray) -> np.ndarray:
            return np.clip((slice_2d - lower) / ww * 255, 0, 255).astype(np.uint8)

        before_slice = slice_to_8bit(volume[cz_idx, :, :])
        after_slice = slice_to_8bit(result_volume[cz_idx, :, :])

        # Side-by-side
        h, w = before_slice.shape
        combined = np.zeros((h, w * 2 + 4), dtype=np.uint8)
        combined[:, :w] = before_slice
        # Divider (light gray line)
        combined[:, w:w + 4] = 160
        combined[:, w + 4:] = after_slice

        # Add labels (original / with lesion) — simple pixel-based label
        # "Original" label at top-left
        combined[4:10, 6:6 + 8 * 6] = 200  # placeholder bar
        # "With Lesion" label at top-right
        combined[4:10, w + 10:w + 10 + 11 * 6] = 200

        img = Image.fromarray(combined, mode="L")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return DicomLesionPreviewResponse(
            image_base64=b64,
            slice_index=cz_idx,
            total_slices=volume.shape[0],
            lesion_center_voxel=[cz_idx, int(round(cy)), int(round(cx))],
            hu_min=stats_hu_min,
            hu_max=stats_hu_max,
            hu_mean=stats_hu_mean,
            hu_std=stats_hu_std,
            voxel_count=stats_voxels,
            volume_mm3=stats_volume_mm3,
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("DICOM lesion preview failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"DICOM lesion preview failed: {str(e)[:300]}",
        )


# ---------------------------------------------------------------------------
# Debug: lesion simulation diagnostics
# ---------------------------------------------------------------------------


@router.post("/debug-lesion", response_model=DebugLesionResponse)
async def debug_lesion(request: DebugLesionRequest):
    """
    Comprehensive lesion simulation debug endpoint.

    Runs the full lesion pipeline (generation → write) and returns
    detailed diagnostics to help pinpoint where bugs occur:

    - Task 1: Lesion generation statistics (voxel count, HU stats)
    - Task 2: Write verification (changed voxels, delta stats)
    - Task 3: Position validation (bounding box, inside-volume check)
    - Task 4: Spacing conversion analysis
    - Task 5: Preview PNG (axial slice at lesion center, base64)

    Returns a JSON object with all diagnostic fields.
    Does NOT create a simulation job or write any files.
    """
    try:
        # ── Resolve volume parameters ──
        volume_shape = tuple(request.volume_shape or [64, 128, 128])
        spacing = tuple(request.spacing or [1.0, 0.5, 0.5])

        # Normalize center: if all zeros, place at volume center
        cz, cy, cx = request.center_z, request.center_y, request.center_x
        if cz == 0.0 and cy == 0.0 and cx == 0.0:
            cz = float(volume_shape[0] // 2)
            cy = float(volume_shape[1] // 2)
            cx = float(volume_shape[2] // 2)

        # ── Build base volume ──
        base_volume = np.full(volume_shape, -1000.0, dtype=np.float32)

        # ── Generate lesion ──
        config_dict = {
            "lesion_type": request.lesion_type,
            "shape": request.shape,
            "center_x": cx,
            "center_y": cy,
            "center_z": cz,
            "radius_x": request.radius_x,
            "radius_y": request.radius_y,
            "radius_z": request.radius_z,
            "hu_mean": request.hu_mean,
            "hu_std": request.hu_std,
            "margin_sharpness": request.margin_sharpness,
            "spiculation_degree": request.spiculation_degree,
        }

        generator = LesionGenerator()
        lesion_vol = generator.generate_lesion(
            volume_shape=volume_shape,
            config=config_dict,
            spacing=spacing,
        )
        lesion_mask = lesion_vol != 0

        # ── Task 1: Lesion generation stats ──
        lesion_voxels = int(np.sum(lesion_mask))
        lesion_ratio = float(lesion_voxels / max(volume_shape[0] * volume_shape[1] * volume_shape[2], 1))
        if lesion_voxels > 0:
            lesion_hu = lesion_vol[lesion_mask]
            lesion_hu_mean = float(np.mean(lesion_hu))
            lesion_hu_min = float(np.min(lesion_hu))
            lesion_hu_max = float(np.max(lesion_hu))
            lesion_hu_std = float(np.std(lesion_hu))
        else:
            lesion_hu_mean = lesion_hu_min = lesion_hu_max = lesion_hu_std = 0.0

        # ── Task 2: Write verification ──
        result_volume = base_volume.copy()
        result_volume[lesion_mask] = lesion_vol[lesion_mask]
        delta = result_volume.astype(np.float32) - base_volume.astype(np.float32)
        changed_voxels = int(np.count_nonzero(delta))
        write_delta_mean = float(np.mean(delta))
        write_delta_max = float(np.max(delta))

        # ── Task 3: Position ──
        nonzero = np.argwhere(lesion_mask)
        if len(nonzero) > 0:
            bbox = {
                "z_min": int(nonzero[:, 0].min()),
                "z_max": int(nonzero[:, 0].max()),
                "y_min": int(nonzero[:, 1].min()),
                "y_max": int(nonzero[:, 1].max()),
                "x_min": int(nonzero[:, 2].min()),
                "x_max": int(nonzero[:, 2].max()),
            }
            inside_volume = (
                bbox["z_min"] >= 0 and bbox["z_max"] < volume_shape[0]
                and bbox["y_min"] >= 0 and bbox["y_max"] < volume_shape[1]
                and bbox["x_min"] >= 0 and bbox["x_max"] < volume_shape[2]
            )
        else:
            bbox = {}
            inside_volume = False

        # ── Task 4: Spacing ──
        radius_voxel = [
            request.radius_z / spacing[0],
            request.radius_y / spacing[1],
            request.radius_x / spacing[2],
        ]
        z_compression_warning = radius_voxel[0] < 2.0

        # ── Task 5: Preview PNG (if matplotlib available) ──
        preview_png_base64 = None
        if HAS_MPL and lesion_voxels > 0:
            cz_idx = int(np.median(nonzero[:, 0])) if len(nonzero) > 0 else int(round(cz))
            cz_idx = max(0, min(cz_idx, volume_shape[0] - 1))

            fig, axes = plt.subplots(2, 2, figsize=(12, 10))
            axes = axes.ravel()

            # Mask
            im0 = axes[0].imshow(lesion_mask[cz_idx, :, :].astype(np.uint8) * 255, cmap="gray", aspect="equal")
            axes[0].set_title(f"Lesion Mask (axial z={cz_idx})")
            plt.colorbar(im0, ax=axes[0], shrink=0.75)

            # Lesion HU
            im1 = axes[1].imshow(lesion_vol[cz_idx, :, :], cmap="viridis", aspect="equal")
            axes[1].set_title(f"Lesion HU (axial z={cz_idx})")
            plt.colorbar(im1, ax=axes[1], shrink=0.75)

            # Result volume
            im2 = axes[2].imshow(result_volume[cz_idx, :, :], cmap="gray", aspect="equal")
            axes[2].set_title(f"Result HU (axial z={cz_idx})")
            plt.colorbar(im2, ax=axes[2], shrink=0.75)

            # Difference map
            diff = np.abs(result_volume.astype(np.float32) - base_volume.astype(np.float32))
            im3 = axes[3].imshow(diff[cz_idx, :, :], cmap="hot", aspect="equal")
            axes[3].set_title(f"|Δ HU| (axial z={cz_idx})")
            plt.colorbar(im3, ax=axes[3], shrink=0.75)

            plt.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", dpi=120, bbox_inches="tight")
            plt.close(fig)
            preview_png_base64 = base64.b64encode(buf.getvalue()).decode("utf-8")

        return DebugLesionResponse(
            lesion_voxels=lesion_voxels,
            lesion_ratio=lesion_ratio,
            lesion_hu_mean=lesion_hu_mean,
            lesion_hu_min=lesion_hu_min,
            lesion_hu_max=lesion_hu_max,
            lesion_hu_std=lesion_hu_std,
            changed_voxels=changed_voxels,
            write_delta_mean=write_delta_mean,
            write_delta_max=write_delta_max,
            volume_shape=list(volume_shape),
            center_voxel=[cz, cy, cx],
            bbox=bbox,
            inside_volume=inside_volume,
            spacing=list(spacing),
            radius_mm=[request.radius_z, request.radius_y, request.radius_x],
            radius_voxel=radius_voxel,
            z_compression_warning=z_compression_warning,
            preview_png_base64=preview_png_base64,
        )

    except Exception as e:
        logger.exception("Debug lesion endpoint failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Debug lesion failed: {str(e)[:500]}",
        )


@router.post("/preview/organ", response_model=SimulationPreviewResponse)
async def preview_organ(config: dict):
    """
    Generate a fast preview of an organ configuration.

    Synchronous endpoint for real-time preview of organ parameters
    without creating a full simulation job.
    """
    try:
        simulator = OrganSimulator()
        preview = simulator.generate_preview(config)
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
# CT Phantom — synthetic upper-body CT volume for frontend demo
# ---------------------------------------------------------------------------


@router.get("/phantom")
async def generate_ct_phantom(
    source: str = Query("procedural", description="Phantom source: 'procedural' or 'atlas'"),
    size: int = Query(192, ge=64, le=320, description="Volume max edge size in voxels"),
    case_id: str = Query("s0001", description="Atlas case ID (only used when source='atlas')"),
    scan_direction: str = Query(
        "head_to_feet",
        description="Z-axis scan direction: 'head_to_feet' (z=0=head/chest) or 'feet_to_head'",
    ),
):
    """
    Generate a CT phantom and return it as base64-encoded volume data.

    Two sources are supported:

    - **procedural** (default):
      Synthetic upper-body CT phantom built from geometric primitives
      (ellipses, arcs).  NOT a real medical image — suitable for UI
      development and demo.

    - **atlas**:
      Loads a real CT volume from models/phantom_atlas/{case_id}/.
      The CT is resampled so its largest dimension ≤ size.  If an
      organs_label.nii.gz file is present it is converted to a uint8
      label map and returned alongside the CT volume.

      The z-axis direction is auto-detected from the NIfTI affine header.
      If it doesn't match `scan_direction` (default: head_to_feet), the
      volume is flipped so z=0 corresponds to the head/chest.

    Returns:
        JSON with:
        - volumeBase64:  base64-encoded raw Float32 bytes (little-endian)
        - labelBase64:   base64-encoded raw Uint8 bytes (optional, atlas only)
        - metadata:      {width, height, depth, spacing, source, case_id?,
                          originalShape, outputShape, originalSpacing,
                          outputSpacing, scanAxis, scanDirection, flippedZ,
                          labelNonzeroCounts?, sliceLabelPresence?,
                          label_map?, windowPresets, bodyThresholdHU}
    """
    try:
        if source == "atlas":
            ct_volume, label_volume, metadata = generate_atlas_ct_phantom(
                case_id=case_id,
                size=size,
                scan_direction=scan_direction,
            )

            # Encode CT volume
            raw_bytes = ct_volume.astype(np.float32).tobytes()
            volume_b64 = base64.b64encode(raw_bytes).decode("ascii")

            response_content: dict = {
                "volume_base64": volume_b64,
                "metadata": metadata,
            }

            # Encode label volume (if present)
            if label_volume is not None:
                label_bytes = label_volume.astype(np.uint8).tobytes()
                label_b64 = base64.b64encode(label_bytes).decode("ascii")
                response_content["label_base64"] = label_b64
            else:
                response_content["label_base64"] = None

            return JSONResponse(content=response_content)

        else:
            # --- procedural (default) ---
            shape = (size, size, size)
            volume, metadata = generate_upper_body_ct_phantom(shape=shape)

            raw_bytes = volume.astype(np.float32).tobytes()
            volume_b64 = base64.b64encode(raw_bytes).decode("ascii")

            metadata["source"] = "procedural"

            return JSONResponse(
                content={
                    "volume_base64": volume_b64,
                    "label_base64": None,
                    "metadata": metadata,
                }
            )

    except FileNotFoundError as e:
        logger.warning("Phantom generation — file not found: %s", e)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        )
    except Exception as e:
        logger.exception("Failed to generate CT phantom")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Phantom generation failed: {str(e)}",
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
