"""Reconstruct nnUNet_raw Dataset703 from preprocessed .npz files.

The original nnUNet_raw data was deleted but the preprocessed data
(E:\nnUNet_preprocessed\Dataset703_LungLobes\nnUNetPlans_3d_fullres\*.npz)
still exists. We reverse the CTNormalization (z-score) to get approximate
HU values and save as NIfTI files in nnUNet_raw format.
"""

import json
import os
import pickle
import time
from pathlib import Path

import numpy as np
import nibabel as nib

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PREPROCESSED = Path(r"E:\nnUNet_preprocessed\Dataset703_LungLobes")
NPZ_DIR = PREPROCESSED / "nnUNetPlans_3d_fullres"
GT_DIR = PREPROCESSED / "gt_segmentations"
RAW_DIR = Path(r"E:\nnUNet_raw\Dataset703_LungLobes")
IMAGES_TR = RAW_DIR / "imagesTr"
LABELS_TR = RAW_DIR / "labelsTr"

# ---------------------------------------------------------------------------
# Normalization parameters (from dataset_fingerprint.json)
# nnUNet CTNormalization: clip to [0.5%, 99.5%], then z-score with global stats
# ---------------------------------------------------------------------------
CT_MEAN = -759.0116927942908
CT_STD = 201.6844295983167

# ---------------------------------------------------------------------------
# Create directory structure
# ---------------------------------------------------------------------------
IMAGES_TR.mkdir(parents=True, exist_ok=True)
LABELS_TR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Write dataset.json (same as original)
# ---------------------------------------------------------------------------
dataset_json = {
    "channel_names": {"0": "CT"},
    "labels": {
        "background": 0,
        "left_upper_lobe": 1,
        "left_lower_lobe": 2,
        "right_upper_lobe": 3,
        "right_middle_lobe": 4,
        "right_lower_lobe": 5,
    },
    "numTraining": 1106,
    "file_ending": ".nii.gz",
    "name": "Dataset703_LungLobes",
}
with open(RAW_DIR / "dataset.json", "w") as f:
    json.dump(dataset_json, f, indent=2)
print(f"[OK] dataset.json written ({RAW_DIR / 'dataset.json'})")

# ---------------------------------------------------------------------------
# Reconstruct each case
# ---------------------------------------------------------------------------
npz_files = sorted(NPZ_DIR.glob("*.npz"))
print(f"Found {len(npz_files)} .npz files to process")

t0 = time.time()
for i, npz_path in enumerate(npz_files):
    case = npz_path.stem  # e.g., "s0000"

    # --- Load preprocessed data ---
    data = np.load(npz_path)
    # data shape: (1, X, Y, Z) — nnUNet internal format (C, X, Y, Z)
    img_norm = data["data"][0].astype(np.float32)   # (X, Y, Z)
    seg = data["seg"][0].astype(np.uint8)            # (X, Y, Z)

    # --- Reverse normalization: z-score -> approximate HU ---
    img_hu = img_norm * CT_STD + CT_MEAN   # ≈ [-1024, 109] HU range

    # --- Load spacing/affine from .pkl ---
    pkl_path = npz_path.with_suffix(".pkl")
    with open(pkl_path, "rb") as f:
        props = pickle.load(f)

    # Use original affine if available, otherwise build from spacing
    try:
        affine = props["nibabel_stuff"]["original_affine"]
    except (KeyError, TypeError):
        spacing = props["spacing"]  # (x, y, z)
        affine = np.diag([*spacing, 1.0])

    # --- Save image (channel 0 -> _0000 suffix) ---
    nii_img = nib.Nifti1Image(img_hu, affine)
    nib.save(nii_img, str(IMAGES_TR / f"{case}_0000.nii.gz"))

    # --- Save label ---
    nii_seg = nib.Nifti1Image(seg, affine)
    nib.save(nii_seg, str(LABELS_TR / f"{case}.nii.gz"))

    if (i + 1) % 200 == 0:
        elapsed = time.time() - t0
        print(f"  [{i+1}/{len(npz_files)}] processed ({elapsed:.0f}s elapsed)")

elapsed = time.time() - t0
print(f"[OK] All {len(npz_files)} cases reconstructed in {elapsed:.0f}s")
print(f"    Images: {IMAGES_TR}")
print(f"    Labels: {LABELS_TR}")

# ---------------------------------------------------------------------------
# Verify: check a few files
# ---------------------------------------------------------------------------
print("\n--- Verification ---")
import random
sample_cases = random.sample([p.stem for p in npz_files], min(3, len(npz_files)))
for case in sample_cases:
    img_path = IMAGES_TR / f"{case}_0000.nii.gz"
    lbl_path = LABELS_TR / f"{case}.nii.gz"
    if img_path.exists() and lbl_path.exists():
        img_size = os.path.getsize(img_path) / 1024 / 1024
        lbl_size = os.path.getsize(lbl_path) / 1024 / 1024
        img_nii = nib.load(str(img_path))
        print(f"  {case}: image={img_nii.shape} {img_size:.1f}MB, label={lbl_size:.1f}MB")
    else:
        print(f"  {case}: MISSING!")

print("\n[OK] Reconstruction complete. Ready for nnUNetv2_plan_and_preprocess.")
