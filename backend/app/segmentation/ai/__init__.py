"""AI-based segmentation pipeline (MONAI integration).

Provides the orchestration layer that combines MONAI model loading,
inference, and postprocessing into a single segmentation workflow.
"""

from app.segmentation.ai.pipeline import (
    run_full_segmentation,
    get_available_organs,
)

__all__ = [
    "run_full_segmentation",
    "get_available_organs",
]
