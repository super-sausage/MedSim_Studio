"""Label definitions for the nnUNet lung lobe model (Dataset703_LungLobes).

The trained model predicts a compact 5-class lung-lobe label space, but the
rest of the MedSim project uses the complete upper-body atlas label space from
the phantom atlas. This module therefore exposes atlas-aligned label IDs and a
helper to remap raw model output into that unified label space.
"""

from typing import Dict, List, Tuple

import numpy as np

# ---------------------------------------------------------------------------
# Model identifier used in API config
# ---------------------------------------------------------------------------
MODEL_NAME = "nnunet_lung_lobe"

# ---------------------------------------------------------------------------
# Raw Dataset703 label map (matches the trained model output)
# ---------------------------------------------------------------------------
LUNG_LOBE_RAW_LABEL_MAP: Dict[str, int] = {
    "background": 0,
    "left_upper_lobe": 1,
    "left_lower_lobe": 2,
    "right_upper_lobe": 3,
    "right_middle_lobe": 4,
    "right_lower_lobe": 5,
}

# ---------------------------------------------------------------------------
# Unified upper-body atlas label map used across the project
# ---------------------------------------------------------------------------
LUNG_LOBE_LABEL_MAP: Dict[str, int] = {
    "background": 0,
    "left_lung_lower_lobe": 10,
    "right_lung_lower_lobe": 11,
    "right_lung_middle_lobe": 12,
    "left_lung_upper_lobe": 13,
    "right_lung_upper_lobe": 14,
}

LUNG_LOBE_RAW_TO_UNIFIED_LABELS: Dict[int, int] = {
    0: 0,
    1: 13,
    2: 10,
    3: 14,
    4: 12,
    5: 11,
}

LUNG_LOBE_LABEL_NAMES: Dict[int, str] = {v: k for k, v in LUNG_LOBE_LABEL_MAP.items()}

# ---------------------------------------------------------------------------
# Human-readable display names for frontend / metadata
# ---------------------------------------------------------------------------
LUNG_LOBE_DISPLAY_NAMES: Dict[int, str] = {
    0: "Background",
    10: "Left Lung Lower Lobe",
    11: "Right Lung Lower Lobe",
    12: "Right Lung Middle Lobe",
    13: "Left Lung Upper Lobe",
    14: "Right Lung Upper Lobe",
}

# ---------------------------------------------------------------------------
# Lung lobe side grouping for UI collapsible sections
# ---------------------------------------------------------------------------
LOBE_SIDE: Dict[int, str] = {
    10: "left",
    13: "left",
    11: "right",
    12: "right",
    14: "right",
}

# ---------------------------------------------------------------------------
# Perceptually distinct colors for each lobe in the unified label space
# ---------------------------------------------------------------------------
LUNG_LOBE_COLORS: Dict[int, Tuple[int, int, int]] = {
    0: (0, 0, 0),
    13: (100, 200, 150),
    10: (60, 160, 100),
    14: (100, 150, 220),
    12: (130, 170, 240),
    11: (70, 100, 200),
}


def remap_lung_lobe_labels_to_upper_body(label_map: np.ndarray) -> np.ndarray:
    """Convert raw 1-5 lung-lobe labels into the atlas 10-14 label IDs."""
    remapped = np.zeros_like(label_map, dtype=np.int32)
    for raw_label, unified_label in LUNG_LOBE_RAW_TO_UNIFIED_LABELS.items():
        if raw_label == 0:
            continue
        remapped[label_map == raw_label] = unified_label
    return remapped


def get_label_defs() -> List[dict]:
    """Build unified upper-body label definitions for API responses."""
    defs = []
    for name, idx in LUNG_LOBE_LABEL_MAP.items():
        defs.append({
            "index": idx,
            "name": LUNG_LOBE_DISPLAY_NAMES.get(idx, name.replace("_", " ").title()),
            "color": list(LUNG_LOBE_COLORS.get(idx, (128, 128, 128))),
        })
    defs.sort(key=lambda x: x["index"])
    return defs
