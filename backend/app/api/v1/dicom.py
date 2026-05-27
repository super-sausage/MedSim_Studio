"""
DICOM Management API

RESTful endpoints for DICOM study, series, and instance management.
Provides CRUD operations plus DICOM file upload, parsing, and serving.
"""

import os
import uuid
import shutil
import logging
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models.dicom import DicomStudy, DicomSeries, DicomInstance
from app.schemas.dicom import (
    DicomStudyResponse,
    DicomSeriesResponse,
    DicomInstanceResponse,
    DicomUploadResponse,
    PaginatedResponse,
)
from app.dicom.parser.dicom_parser import DicomParser
from app.core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dicom", tags=["DICOM"])


@router.get("/studies", response_model=PaginatedResponse)
async def list_studies(
    page: int = Query(1, ge=1, description="Page number"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by patient name or ID"),
    db: Session = Depends(get_db),
):
    """
    List all DICOM studies with pagination and optional search.
    Returns basic study metadata for the study list view.
    """
    query = db.query(DicomStudy)

    if search:
        search_filter = f"%{search}%"
        query = query.filter(
            DicomStudy.patient_name.ilike(search_filter) |
            DicomStudy.patient_id.ilike(search_filter)
        )

    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    studies = query.order_by(DicomStudy.created_at.desc()) \
        .offset((page - 1) * page_size) \
        .limit(page_size) \
        .all()

    return PaginatedResponse(
        items=[DicomStudyResponse.model_validate(s) for s in studies],
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
    )


@router.get("/studies/{study_id}", response_model=DicomStudyResponse)
async def get_study(study_id: str, db: Session = Depends(get_db)):
    """Get detailed information about a specific study."""
    study = db.query(DicomStudy).filter(DicomStudy.id == study_id).first()
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study {study_id} not found",
        )
    return study


@router.get("/studies/{study_id}/series", response_model=List[DicomSeriesResponse])
async def get_study_series(study_id: str, db: Session = Depends(get_db)):
    """Get all series within a study."""
    study = db.query(DicomStudy).filter(DicomStudy.id == study_id).first()
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study {study_id} not found",
        )
    return study.series


@router.get("/studies/{study_id}/series/{series_id}", response_model=DicomSeriesResponse)
async def get_series_detail(study_id: str, series_id: str, db: Session = Depends(get_db)):
    """Get detailed information about a specific series."""
    series = db.query(DicomSeries).filter(
        DicomSeries.id == series_id,
        DicomSeries.study_id == study_id,
    ).first()
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Series {series_id} not found in study {study_id}",
        )
    return series


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------


@router.get(
    "/series/{series_id}/instances",
    response_model=List[DicomInstanceResponse],
)
async def get_series_instances(series_id: str, db: Session = Depends(get_db)):
    """
    Get all DICOM instances (slices) within a series.
    Ordered by instance number for correct slice ordering.
    """
    series = db.query(DicomSeries).filter(DicomSeries.id == series_id).first()
    if not series:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Series {series_id} not found",
        )

    instances = (
        db.query(DicomInstance)
        .filter(DicomInstance.series_id == series_id)
        .order_by(DicomInstance.instance_number.asc().nulls_last())
        .all()
    )

    return [
        DicomInstanceResponse.model_validate(inst) for inst in instances
    ]


@router.get("/instances/{instance_id}/file")
async def get_instance_file(instance_id: str, db: Session = Depends(get_db)):
    """
    Serve the raw DICOM file for an instance.
    Used by Cornerstone3D's wadouri image loader to load image data.
    """
    instance = db.query(DicomInstance).filter(
        DicomInstance.id == instance_id
    ).first()
    if not instance:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Instance {instance_id} not found",
        )

    if not instance.pixel_data_path or not os.path.exists(instance.pixel_data_path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"DICOM file not found on disk for instance {instance_id}",
        )

    return FileResponse(
        path=instance.pixel_data_path,
        media_type="application/dicom",
        filename=os.path.basename(instance.pixel_data_path),
    )


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=DicomUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_dicom(
    files: List[UploadFile] = File(..., description="DICOM files to upload"),
    study_id: Optional[str] = Query(None, description="Optional study ID to associate files with"),
    db: Session = Depends(get_db),
):
    """
    Upload and parse DICOM files.

    Accepts multiple DICOM files, parses them, extracts metadata,
    stores DICOM files persistently on disk, and creates database records.
    Supports standard DICOM and DICOMDIR formats.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Use a temp staging area for upload, then move to persistent storage
    upload_id = str(uuid.uuid4())
    staging_dir = f"{settings.UPLOAD_DIR}/{upload_id}"
    os.makedirs(staging_dir, exist_ok=True)

    saved_paths = []
    try:
        # Save uploaded files to staging directory
        for file in files:
            content = await file.read()
            file_name = file.filename or f"dicom_{uuid.uuid4()}.dcm"
            file_path = os.path.join(staging_dir, file_name)
            with open(file_path, "wb") as f:
                f.write(content)
            saved_paths.append(file_path)

        # Parse DICOM files to extract metadata
        parser = DicomParser()
        result = parser.parse_files(saved_paths, db)

        # Move DICOM files from staging to persistent storage
        study_persistent_dir = f"{settings.DICOM_STORAGE_DIR}/{result['study_id']}"
        os.makedirs(study_persistent_dir, exist_ok=True)

        # Update pixel_data_path for all instances in this study
        series_list = (
            db.query(DicomSeries)
            .filter(DicomSeries.study_id == result["study_id"])
            .all()
        )

        for series in series_list:
            series_storage = f"{study_persistent_dir}/{series.id}"
            os.makedirs(series_storage, exist_ok=True)

            instances = (
                db.query(DicomInstance)
                .filter(DicomInstance.series_id == series.id)
                .all()
            )

            for inst in instances:
                old_path = inst.pixel_data_path
                if old_path and os.path.exists(old_path):
                    # Preserve original filename for the DICOM file
                    orig_name = os.path.basename(old_path)
                    new_path = os.path.join(series_storage, orig_name)

                    # Handle duplicate filenames
                    counter = 1
                    while os.path.exists(new_path):
                        name_parts = os.path.splitext(orig_name)
                        new_path = os.path.join(
                            series_storage, f"{name_parts[0]}_{counter}{name_parts[1]}"
                        )
                        counter += 1

                    shutil.move(old_path, new_path)
                    inst.pixel_data_path = new_path

            # Update series storage path
            series.storage_path = series_storage

        db.commit()

        return DicomUploadResponse(
            study_id=result["study_id"],
            series_count=result["series_count"],
            instance_count=result["instance_count"],
        )

    except Exception as e:
        logger.error(f"Upload failed: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to process DICOM files: {str(e)}",
        )
    finally:
        # Clean up staging directory only
        if os.path.exists(staging_dir):
            shutil.rmtree(staging_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------


@router.delete("/studies/{study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study(study_id: str, db: Session = Depends(get_db)):
    """Delete a study and all associated series, instances, and files."""
    study = db.query(DicomStudy).filter(DicomStudy.id == study_id).first()
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study {study_id} not found",
        )

    # Remove persistent DICOM files from disk
    study_dir = f"{settings.DICOM_STORAGE_DIR}/{study_id}"
    if os.path.exists(study_dir):
        shutil.rmtree(study_dir, ignore_errors=True)

    db.delete(study)
    db.commit()
