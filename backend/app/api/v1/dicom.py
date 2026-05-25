"""
DICOM Management API

RESTful endpoints for DICOM study and series management.
Provides CRUD operations for studies, series, and instances,
plus DICOM file upload and parsing.
"""

import os
import uuid
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query, status
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


@router.post("/upload", response_model=DicomUploadResponse, status_code=status.HTTP_201_CREATED)
async def upload_dicom(
    files: List[UploadFile] = File(..., description="DICOM files to upload"),
    study_id: Optional[str] = Query(None, description="Optional study ID to associate files with"),
    db: Session = Depends(get_db),
):
    """
    Upload and parse DICOM files.

    Accepts multiple DICOM files, parses them, extracts metadata,
    and stores them in the database and object storage.
    Supports standard DICOM and DICOMDIR formats.
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No files provided",
        )

    # Save uploaded files to temporary directory
    upload_dir = f"/tmp/uploads/{uuid.uuid4()}"
    os.makedirs(upload_dir, exist_ok=True)

    saved_paths = []
    for file in files:
        content = await file.read()
        file_path = os.path.join(upload_dir, file.filename or f"dicom_{uuid.uuid4()}.dcm")
        with open(file_path, "wb") as f:
            f.write(content)
        saved_paths.append(file_path)

    # Parse DICOM files
    parser = DicomParser()

    try:
        result = parser.parse_files(saved_paths, db)
    finally:
        # Cleanup temp files
        for path in saved_paths:
            if os.path.exists(path):
                os.remove(path)
        if os.path.exists(upload_dir):
            os.rmdir(upload_dir)

    return DicomUploadResponse(
        study_id=result.get("study_id", ""),
        series_count=result.get("series_count", 0),
        instance_count=result.get("instance_count", 0),
    )


@router.delete("/studies/{study_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_study(study_id: str, db: Session = Depends(get_db)):
    """Delete a study and all associated series and instances."""
    study = db.query(DicomStudy).filter(DicomStudy.id == study_id).first()
    if not study:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Study {study_id} not found",
        )
    db.delete(study)
    db.commit()
