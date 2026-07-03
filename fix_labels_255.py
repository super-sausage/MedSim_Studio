"""Fix labels: map 255 → 0 (background) in gt_segmentations and raw labelsTr."""
import os
import time
import nibabel as nib
import numpy as np

paths = [
    r"E:\nnUNet_preprocessed\Dataset703_LungLobes\gt_segmentations",
    r"E:\nnUNet_raw\Dataset703_LungLobes\labelsTr",
]

total_fixed = 0
for base in paths:
    if not os.path.isdir(base):
        print(f"[SKIP] {base} not found")
        continue
    files = sorted([f for f in os.listdir(base) if f.endswith('.nii.gz')])
    print(f"[Scanning {base}] {len(files)} files...")
    t0 = time.time()
    fixed_count = 0
    for fname in files:
        fpath = os.path.join(base, fname)
        img = nib.load(fpath)
        data = img.get_fdata()
        has_255 = np.any(data == 255)
        if has_255:
            data[data == 255] = 0
            nib.save(nib.Nifti1Image(data.astype(np.uint8), img.affine), fpath)
            fixed_count += 1
    elapsed = time.time() - t0
    print(f"  Fixed {fixed_count} files (255->0) in {elapsed:.0f}s")
    total_fixed += fixed_count

print(f"\n[OK] Total files fixed: {total_fixed}")
print("\nNow delete old preprocessed data and re-run preprocessing.")
