"""
Application Configuration

Central configuration management using pydantic-settings.
Reads from environment variables with sensible defaults
for development, production-ready defaults for deployment.
"""

from pydantic_settings import BaseSettings
from typing import List
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "CT Simulator API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = True
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    CORS_ORIGINS: List[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
    ]

    # Database (PostgreSQL)
    POSTGRES_HOST: str = "postgres"
    POSTGRES_PORT: int = 5432
    POSTGRES_DB: str = "ct_simulator"
    POSTGRES_USER: str = "ctuser"
    POSTGRES_PASSWORD: str = "ctpass123"
    DATABASE_URL: str = (
        f"postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}"
        f"@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
    )

    # MinIO (Object Storage)
    MINIO_HOST: str = "minio"
    MINIO_PORT: int = 9000
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin123"
    MINIO_BUCKET: str = "dicom-studies"
    MINIO_URL: str = "http://minio:9000"

    # Upload
    UPLOAD_DIR: str = "/tmp/uploads"
    MAX_UPLOAD_SIZE: int = 500 * 1024 * 1024  # 500MB

    # DICOM file storage (persistent — not cleaned after upload)
    DICOM_STORAGE_DIR: str = "/app/static/dicom"

    # AI Services
    AI_MONAI_ENABLED: bool = False
    AI_DEVICE: str = "cpu"
    AI_MODEL_PATH: str = "/app/ai-models"

    # Simulation
    SIMULATION_DEFAULT_SEED: int = 42
    SIMULATION_MAX_LESIONS: int = 50
    SIMULATION_VOXEL_SIZE: float = 0.5

    # Rendering
    RENDERING_CACHE_SIZE: int = 512
    RENDERING_QUALITY: str = "high"

    # Logging
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"
        case_sensitive = True


settings = Settings()
