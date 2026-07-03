"""Label definitions for the nnUNet lung lobe model (Dataset703_LungLobes).

Trained on TotalSegmentator data for 5 lung lobes:
  left_upper_lobe, left_lower_lobe,
  right_upper_lobe, right_middle_lobe, right_lower_lobe

Useful for surgical planning, lobectomy simulation, and pulmonary function analysis.
"""

from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# Model identifier used in API config
# ---------------------------------------------------------------------------
MODEL_NAME = "nnunet_lung_lobe"

# ---------------------------------------------------------------------------
# Label index → name mapping (matching dataset.json)
# ---------------------------------------------------------------------------
LUNG_LOBE_LABEL_MAP: Dict[str, int] = {
    "background": 0,
    "left_upper_lobe": 1,
    "left_lower_lobe": 2,
    "right_upper_lobe": 3,
    "right_middle_lobe": 4,
    "right_lower_lobe": 5,
}

# Reverse lookup
LUNG_LOBE_LABEL_NAMES: Dict[int, str] = {v: k for k, v in LUNG_LOBE_LABEL_MAP.items()}

# ---------------------------------------------------------------------------
# Human-readable display names (Chinese + English for frontend)
# ---------------------------------------------------------------------------
LUNG_LOBE_DISPLAY_NAMES: Dict[int, str] = {
    0: "Background",
    1: "Left Upper Lobe (左上叶)",
    2: "Left Lower Lobe (左下叶)",
    3: "Right Upper Lobe (右上叶)",
    4: "Right Middle Lobe (右中叶)",
    5: "Right Lower Lobe (右下叶)",
}

# ---------------------------------------------------------------------------
# Lung lobe side grouping for UI collapsible sections
# ---------------------------------------------------------------------------
LOBE_SIDE: Dict[int, str] = {
    1: "left",
    2: "left",
    3: "right",
    4: "right",
    5: "right",
}

# ---------------------------------------------------------------------------
# Perceptually distinct colors for each lobe
# Use a sequential/saturation scheme within each lung
# ---------------------------------------------------------------------------
LUNG_LOBE_COLORS: Dict[int, Tuple[int, int, int]] = {
    0: (0, 0, 0),                     # Background — black
    # Left lung — green/teal tones
    1: (100, 200, 150),               # Left upper — mint
    2: (60, 160, 100),                # Left lower — forest green
    # Right lung — blue/purple tones
    3: (100, 150, 220),               # Right upper — sky blue
    4: (130, 170, 240),               # Right middle — light blue
    5: (70, 100, 200),                # Right lower — steel blue
}


def get_label_defs() -> List[dict]:
    """Build label definitions list for API responses."""
    defs = []
    for name, idx in LUNG_LOBE_LABEL_MAP.items():
        defs.append({
            "index": idx,
            "name": LUNG_LOBE_DISPLAY_NAMES.get(idx, name.replace("_", " ").title()),
            "color": list(LUNG_LOBE_COLORS.get(idx, (128, 128, 128))),
        })
    defs.sort(key=lambda x: x["index"])
    return defs
