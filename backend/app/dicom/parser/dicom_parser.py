"""
DICOM File Parser

Handles parsing of DICOM files, metadata extraction, and database storage.
Supports standard DICOM files and extracts key medical imaging parameters
for visualization and processing pipelines.
"""

import os
import uuid
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime

import pydicom
from pydicom.dataset import Dataset, FileMetaDataset
from sqlalchemy.orm import Session

from app.models.dicom import DicomStudy, DicomSeries, DicomInstance

logger = logging.getLogger(__name__)


class DicomParserError(Exception):
    """Custom exception for DICOM parsing errors."""
    pass


class DicomParser:
    """
    Parser for DICOM medical imaging files.

    Extracts metadata from DICOM files and creates corresponding
    database records for studies, series, and instances.
    """

    # Required DICOM tags for study level
    STUDY_TAGS = [
        "PatientName", "PatientID", "PatientBirthDate", "PatientSex",
        "StudyInstanceUID", "StudyDate", "StudyTime", "StudyDescription",
        "AccessionNumber", "ReferringPhysicianName",
    ]

    # Required DICOM tags for series level
    SERIES_TAGS = [
        "SeriesInstanceUID", "SeriesNumber", "SeriesDescription",
        "Modality", "Manufacturer", "BodyPartExamined",
        "Laterality", "ProtocolName",
    ]

    # Required DICOM tags for instance level
    INSTANCE_TAGS = [
        "SOPInstanceUID", "InstanceNumber",
        "Rows", "Columns", "SliceLocation",
    ]

    def parse_files(self, file_paths: List[str], db: Session) -> Dict[str, Any]:
        """
        Parse a list of DICOM files and store metadata in the database.

        Args:
            file_paths: List of paths to DICOM files
            db: SQLAlchemy database session

        Returns:
            Dictionary with study_id, series_count, and instance_count

        Raises:
            DicomParserError: If parsing fails for all files
        """
        if not file_paths:
            raise DicomParserError("No files provided for parsing")

        # Group files by StudyInstanceUID
        studies_data: Dict[str, Dict] = {}
        series_data: Dict[str, Dict] = {}
        instances: List[Dict] = []

        for file_path in file_paths:
            try:
                ds = pydicom.dcmread(file_path, force=True)
                self._validate_dataset(ds)

                study_uid = ds.StudyInstanceUID
                series_uid = ds.SeriesInstanceUID

                if study_uid not in studies_data:
                    studies_data[study_uid] = self._extract_study_metadata(ds)

                if series_uid not in series_data:
                    series_data[series_uid] = self._extract_series_metadata(ds, file_path)

                instances.append(self._extract_instance_metadata(ds, file_path))

            except Exception as e:
                logger.warning(f"Failed to parse {file_path}: {e}")
                continue

        if not studies_data:
            raise DicomParserError("No valid DICOM files found")

        # Store in database
        study_id = self._store_studies(studies_data, series_data, instances, db)

        return {
            "study_id": study_id,
            "series_count": len(series_data),
            "instance_count": len(instances),
        }

    def _validate_dataset(self, ds: Dataset) -> None:
        """Validate that the dataset contains required DICOM tags."""
        required_tags = ["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"]
        missing = [tag for tag in required_tags if not hasattr(ds, tag)]
        if missing:
            raise DicomParserError(f"Missing required tags: {missing}")

    def _extract_study_metadata(self, ds: Dataset) -> Dict[str, Any]:
        """Extract study-level metadata from a DICOM dataset."""
        return {
            "patient_id": self._get_tag(ds, "PatientID", "UNKNOWN"),
            "patient_name": str(getattr(ds, "PatientName", "UNKNOWN")),
            "patient_birth_date": self._get_tag(ds, "PatientBirthDate"),
            "patient_sex": self._get_tag(ds, "PatientSex"),
            "study_instance_uid": ds.StudyInstanceUID,
            "study_date": self._get_tag(ds, "StudyDate"),
            "study_time": self._get_tag(ds, "StudyTime"),
            "study_description": self._get_tag(ds, "StudyDescription"),
            "accession_number": self._get_tag(ds, "AccessionNumber"),
            "referring_physician": str(getattr(ds, "ReferringPhysicianName", "")),
            "modality": self._get_tag(ds, "Modality"),
        }

    def _extract_series_metadata(self, ds: Dataset, file_path: str) -> Dict[str, Any]:
        """Extract series-level metadata from a DICOM dataset."""
        pixel_spacing = None
        if hasattr(ds, "PixelSpacing"):
            pixel_spacing = [float(v) for v in ds.PixelSpacing]

        # Calculate window center/width if available
        window_center = None
        window_width = None
        if hasattr(ds, "WindowCenter"):
            wc = ds.WindowCenter
            window_center = float(wc[0]) if isinstance(wc, list) else float(wc)
        if hasattr(ds, "WindowWidth"):
            ww = ds.WindowWidth
            window_width = float(ww[0]) if isinstance(ww, list) else float(ww)

        return {
            "series_instance_uid": ds.SeriesInstanceUID,
            "series_number": self._get_int_tag(ds, "SeriesNumber"),
            "series_description": self._get_tag(ds, "SeriesDescription"),
            "modality": self._get_tag(ds, "Modality"),
            "manufacturer": self._get_tag(ds, "Manufacturer"),
            "body_part_examined": self._get_tag(ds, "BodyPartExamined"),
            "laterality": self._get_tag(ds, "Laterality"),
            "protocol_name": self._get_tag(ds, "ProtocolName"),
            "rows": self._get_int_tag(ds, "Rows"),
            "columns": self._get_int_tag(ds, "Columns"),
            "slice_thickness": self._get_float_tag(ds, "SliceThickness"),
            "pixel_spacing": pixel_spacing,
            "window_center": window_center,
            "window_width": window_width,
            "storage_path": file_path,
        }

    def _extract_instance_metadata(self, ds: Dataset, file_path: str) -> Dict[str, Any]:
        """Extract instance-level metadata from a DICOM dataset."""
        image_position = None
        if hasattr(ds, "ImagePositionPatient"):
            image_position = [float(v) for v in ds.ImagePositionPatient]

        image_orientation = None
        if hasattr(ds, "ImageOrientationPatient"):
            image_orientation = [float(v) for v in ds.ImageOrientationPatient]

        return {
            "sop_instance_uid": ds.SOPInstanceUID,
            "instance_number": self._get_int_tag(ds, "InstanceNumber"),
            "image_position": image_position,
            "image_orientation": image_orientation,
            "slice_location": self._get_float_tag(ds, "SliceLocation"),
            "rows": self._get_int_tag(ds, "Rows"),
            "columns": self._get_int_tag(ds, "Columns"),
            "pixel_data_path": file_path,
            "file_size": os.path.getsize(file_path),
        }

    def _store_studies(
        self,
        studies_data: Dict[str, Dict],
        series_data: Dict[str, Dict],
        instances: List[Dict],
        db: Session,
    ) -> str:
        """Store parsed DICOM data in the database."""
        first_study_uid = list(studies_data.keys())[0]
        study_info = studies_data[first_study_uid]

        # Check if study already exists
        existing = db.query(DicomStudy).filter(
            DicomStudy.study_instance_uid == first_study_uid
        ).first()

        if existing:
            study_id = existing.id
            logger.info(f"Appending to existing study {study_id}")
        else:
            study_id = str(uuid.uuid4())
            study = DicomStudy(
                id=study_id,
                **study_info,
                modalities=[study_info.get("modality", "")] if study_info.get("modality") else [],
                series_count=len(series_data),
                instance_count=len(instances),
            )
            db.add(study)

        # Store series
        for series_uid, series_info in series_data.items():
            existing_series = db.query(DicomSeries).filter(
                DicomSeries.series_instance_uid == series_uid
            ).first()

            if not existing_series:
                series = DicomSeries(
                    id=str(uuid.uuid4()),
                    study_id=study_id,
                    **series_info,
                    file_count=sum(
                        1 for inst in instances
                        if inst["sop_instance_uid"] != "UNKNOWN"
                    ),
                )
                db.add(series)

        # Store instances
        for inst_info in instances:
            existing_instance = db.query(DicomInstance).filter(
                DicomInstance.sop_instance_uid == inst_info["sop_instance_uid"]
            ).first()

            if not existing_instance:
                series = db.query(DicomSeries).filter(
                    DicomSeries.series_instance_uid == inst_info.get("series_instance_uid", "")
                ).first()

                if series:
                    instance = DicomInstance(
                        id=str(uuid.uuid4()),
                        series_id=series.id,
                        **inst_info,
                    )
                    db.add(instance)

        db.commit()
        return study_id

    @staticmethod
    def _get_tag(ds: Dataset, tag_name: str, default: Optional[str] = None) -> Optional[str]:
        """Safely get a DICOM tag value, returning default if not present."""
        value = getattr(ds, tag_name, default)
        if value is None:
            return default
        return str(value)

    @staticmethod
    def _get_int_tag(ds: Dataset, tag_name: str) -> Optional[int]:
        """Safely get an integer DICOM tag value."""
        value = getattr(ds, tag_name, None)
        if value is None:
            return None
        try:
            return int(value)
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _get_float_tag(ds: Dataset, tag_name: str) -> Optional[float]:
        """Safely get a float DICOM tag value."""
        value = getattr(ds, tag_name, None)
        if value is None:
            return None
        try:
            return float(value)
        except (ValueError, TypeError):
            return None
