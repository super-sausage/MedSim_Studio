"""Custom nnUNet lung lobe model inference — Dataset703_LungLobes.

Exposes:
  - run_nnunet_lung_lobe(): main inference entry point
  - is_available(): check that the model folder exists
  - CustomModelNotAvailableError
  - remap_lung_lobe_labels_to_upper_body(): remap raw 5-lobe labels into the
    unified upper-body atlas label space used elsewhere in the project
"""

from app.ai.nnunet_lung_lobe.segmenter import (
    run_nnunet_lung_lobe,
    is_available,
    CustomModelNotAvailableError,
    warmup_nnunet_lung_lobe,
)
from app.ai.nnunet_lung_lobe.labels import remap_lung_lobe_labels_to_upper_body

__all__ = [
    "run_nnunet_lung_lobe",
    "is_available",
    "CustomModelNotAvailableError",
    "warmup_nnunet_lung_lobe",
    "remap_lung_lobe_labels_to_upper_body",
]
