"""
Health Check API

Provides service health monitoring endpoints for Docker
orchestration and load balancer health probes.
"""

from fastapi import APIRouter, status
from datetime import datetime
from app import __version__, __app_name__

router = APIRouter(tags=["Health"])


@router.get("/health", status_code=status.HTTP_200_OK)
async def health_check():
    """
    Basic health check endpoint.
    Returns service status and version information.
    Used by Docker health checks and monitoring systems.
    """
    return {
        "status": "healthy",
        "app_name": __app_name__,
        "version": __version__,
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/health/ready", status_code=status.HTTP_200_OK)
async def readiness_check():
    """
    Readiness probe endpoint.
    Checks if the service is ready to accept traffic.
    Verifies database connectivity and storage availability.
    """
    # TODO: Add actual readiness checks:
    # - Database connection pool health
    # - MinIO bucket accessibility
    # - Volume rendering cache status
    return {
        "status": "ready",
        "checks": {
            "database": "pending",
            "storage": "pending",
            "cache": "pending",
        },
        "timestamp": datetime.utcnow().isoformat(),
    }


@router.get("/health/live", status_code=status.HTTP_200_OK)
async def liveness_check():
    """
    Liveness probe endpoint.
    Basic process liveness indicator.
    """
    return {
        "status": "alive",
        "timestamp": datetime.utcnow().isoformat(),
    }
