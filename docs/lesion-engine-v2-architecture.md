# Lesion Engine V2 Architecture

> **Author:** Principal Architect
> **Date:** 2026-07-06
> **Status:** Design Proposal — Awaiting Approval
> **Scope:** Backend simulation engine refactoring & extension

---

## Table of Contents

1. [Phase 1: Codebase Audit](#phase-1-codebase-audit)
2. [Phase 2: Redundancy Detection](#phase-2-redundancy-detection)
3. [Phase 3: Compatibility Analysis](#phase-3-compatibility-analysis)
4. [Phase 4: Architecture Design](#phase-4-architecture-design)
5. [Data Flow](#data-flow)
6. [API Changes](#api-changes)
7. [Database Changes](#database-changes)
8. [Migration Path](#migration-path)

---

## Phase 1: Codebase Audit

### 1.1 Project Technology Stack

| Layer | Technology |
|-------|-----------|
| Frontend | React + TypeScript + Cornerstone3D + vtk.js |
| Backend | FastAPI + SimpleITK + NumPy + SciPy |
| Storage | MinIO / Local filesystem |
| Serialization | pydicom + nibabel + NRRD |
| AI Pipeline | nnUNet (segmentation) |

---

### 1.2 `simulation/` — Directory Tree

```
backend/app/simulation/
├── __init__.py                          # Package declaration
├── exporter.py                          # Simulation result export
├── phantom_generator.py                 # CT phantom (procedural + atlas)
├── volume_builder.py                    # 3D volume from DICOM or synthetic
│
├── deformation/
│   ├── __init__.py
│   └── field.py                         # B-spline deformation field
│
├── hu/
│   ├── __init__.py
│   └── modifier.py                      # HU value operations
│
├── lesion/
│   ├── __init__.py
│   ├── generator.py                     # Core lesion generator
│   └── DEBUG_REPORT.md                  # Bug diagnostic report
│
└── organ/
    ├── __init__.py
    └── simulator.py                     # Organ simulation
```

---

### 1.3 Module-by-Module Analysis

#### `exporter.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | NRRD direct download; NRRD→NIfTI conversion (SimpleITK); NRRD→DICOM zip (pydicom); Streaming `StreamingResponse` + temp cleanup |
| **Core interfaces** | `export_nrrd(job, storage)`, `export_nifti(job, storage)`, `export_dicom_zip(job, storage)` |
| **Missing features** | — Selective export (lesion mask / organ mask only) — Mesh export (.stl / .obj) |
| **Reusable** | `_build_slice_dicom()` DICOM serialization pattern; `stream_file_response()` + temp cleanup pattern 【复用现有模块】 |

#### `phantom_generator.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | **Dual-mode CT phantom generator**: (1) Procedural — geometric primitives approximating upper-body anatomy (lungs, spine, ribs, heart, liver, spleen, kidneys, aorta, trachea) (2) Atlas-based — loads real CT NIfTI + 20-channel organ label map; NIfTI affine direction detection / z-flip; isotropic resampling; 20-class `ORGAN_LABEL_MAP` hardcoded |
| **Core interfaces** | `generate_upper_body_ct_phantom(shape, spacing, seed)` → `(volume, metadata)`; `generate_atlas_ct_phantom(case_id, size, scan_direction)` → `(ct_volume, label_volume, metadata)` |
| **Missing features** | Atlas only supports fixed `models/phantom_atlas/` format; no atlas preview/management API; no atlas data validation; no multi-atlas fusion |
| **Reusable** | 🔑 **`ORGAN_LABEL_MAP`** (20 organ index→name mapping) reusable by OrganAwarePlacement; atlas loading pipeline (nibabel → transpose → resample → flip); procedural CT generation logic 【复用现有模块】 |

#### `volume_builder.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | Read DICOM from storage → parse pixel data → HU conversion (RescaleSlope/Intercept) → sort by z → stack 3D; synthetic fallback (single ellipsoid) |
| **Core interfaces** | `build_volume_from_dicom(storage, instances)` → `(volume, metadata)`; `build_synthetic_volume(shape, spacing)` → `(volume, metadata)` |
| **Missing features** | No incremental volume building (must load all slices at once); no volume cropping / ROI extraction |
| **Reusable** | Metadata dict structure (spacing, origin, direction) compatible with SimpleITK and consumable by both exporter and vtk.js 【复用现有模块】 |

#### `lesion/generator.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | 5 lesion types (tumor/nodule/cyst/calcification/metastasis) + HU defaults; 4 shapes (spherical/lobulated/spiculated/irregular); soft margin (sigmoid transition); mm→voxel radius conversion (spacing-aware); HU gaussian noise → internal heterogeneity; 32³ preview mode |
| **Core class** | `LesionGenerator(rng_seed)` |
| **Core interfaces** | `generate_lesion(volume_shape, config, spacing)` → `np.ndarray`; `generate_preview(config)` → `dict` (statistics) |
| **Missing features** | ❌ No Mesh input (voxel-only) ❌ No texture generation (HU noise only) ❌ No internal calcification/necrosis (TODO marker) ❌ No multi-lesion overlap handling |
| **Reusable** | Distance field computation; sigmoid soft margin; shape deformation algorithms (spiculation/lobulation adaptable to Mesh pipeline) 【复用现有模块】 |

#### `organ/simulator.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | 9 organ types (liver/kidney/lung/brain/bone/heart/spleen/pancreas/bladder); ellipsoid/superquadric shapes; smooth edges (gaussian_filter + binary_fill_holes); HU noise + texture; enhancement modes (homogeneous/heterogeneous/rim/septal) |
| **Core class** | `OrganSimulator(rng_seed)` |
| **Core interfaces** | `generate_organ(volume_shape, config)` → `np.ndarray`; `generate_preview(config)` → `dict` |
| **Missing features** | ❌ Organ shapes are simple geometric primitives (not anatomical) ❌ No inter-organ spatial relations (liver overlaps kidney?) ❌ No organ deformation |
| **Reusable** | 🔑 `ORGAN_HU_RANGES` dict (typical HU per organ); `_apply_enhancement()` contrast pattern logic 【复用现有模块】 |

#### `hu/modifier.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | 4 HU operations (add/subtract/replace/scale); contrast enhancement; gaussian/poisson noise; beam-hardening artifact (cupping effect) |
| **Core class** | `HUModifier` (all static methods) |
| **Missing features** | ❌ No metal artifact ❌ No partial volume effect ❌ No motion artifact |
| **Reusable** | `apply_operation()` can serve as backend for blend writer; `add_contrast()` / `add_noise()` as texture utility functions 【复用现有模块】 |

#### `deformation/field.py`

| Aspect | Status |
|--------|--------|
| **Existing features** | B-spline deformation field generation; `map_coordinates` interpolation deformation; Jacobian determinant computation (detect expansion/contraction) |
| **Core class** | `DeformationField(shape)` |
| **Core interfaces** | `generate_bspline_field(spacing, magnitude, sigma)` → `np.ndarray`; `apply_deformation(volume)` → `np.ndarray`; `compute_jacobian_determinant()` → `np.ndarray` |
| **Missing features** | ❌ No Demons registration ❌ No temporal motion sequences ❌ No organ-specific deformation constraints |
| **Reusable** | 🔑 B-spline field reusable for both tissue and lesion deformation; Jacobian for deformation validation 【复用现有模块】 |

#### API Layer — `api/v1/simulation.py`

| Endpoint | Function | Status |
|----------|----------|--------|
| `POST /api/v1/simulation/jobs` | Create simulation job | ✅ |
| `GET /api/v1/simulation/jobs` | List jobs | ✅ |
| `GET /api/v1/simulation/jobs/{id}` | Get job | ✅ |
| `POST /api/v1/simulation/jobs/{id}/cancel` | Cancel job | ✅ |
| `POST /api/v1/simulation/preview/lesion` | Lesion preview | ✅ |
| `POST /api/v1/simulation/preview/lesion-on-dicom` | DICOM lesion preview (with write) | ✅ |
| `POST /api/v1/simulation/debug-lesion` | Lesion debug (4 checks) | ✅ |
| `POST /api/v1/simulation/preview/organ` | Organ preview | ✅ |
| `GET /api/v1/simulation/phantom` | Get CT phantom | ✅ |
| `GET /api/v1/simulation/jobs/{id}/export` | Export results | ✅ |

#### Frontend 3D — Current Capability

| Engine | Capability | Status |
|--------|-----------|--------|
| **vtk.js** | Volume rendering (4 CT presets); segmentation overlay (color + opacity); clipping planes; synthetic + DICOM mode; trackball camera; brightness/contrast controls | ✅ Production-ready |
| **Cornerstone3D** | MPR tri-planar views; segmentation label management; brush tool; full init pipeline (WebGL recovery, tool registration, web workers) | ✅ Production-ready |

**Conclusion:** No new 3D engine needed. vtk.js + Cornerstone3D covers all rendering requirements.

---

## Phase 2: Redundancy Detection

### 2.1 Proposed Modules vs Existing Capabilities

| Proposed Module | Already Exists? | Existing Location | Should Extend | Should Create New |
|---|---|---|---|---|
| **MeshLesionGenerator** | ❌ No | — | No — existing `LesionGenerator` is purely voxel-based | ✅ **New `lesion/mesh_generator.py`** |
| **TextureGenerator** | ⚠️ Partial | `LesionGenerator` (HU noise), `OrganSimulator` (gaussian texture), `HUModifier` (noise/artifact) | ✅ **Extend `HUModifier` or create `lesion/texture.py`** | If texture requirements are complex (Perlin + fractal + organ-specific), prefer new module |
| **OrganAwarePlacement** | ⚠️ Partial | `phantom_generator.ORGAN_LABEL_MAP` (20 labels), atlas CT (organ mask), `organ/simulator._get_organ_shape_params()` (geometry) | ✅ **Extend existing modules first**, reusing atlas label mapping and OrganSimulator shape params | If logic is complex (vessel avoidance, multi-lesion co-existence, organ boundary constraints), create `simulation/placement/` |
| **LesionAnalyzer** | ⚠️ Partial | `debug_lesion()` API endpoint (4 checks: generation stats, write verification, position, spacing); `generate_preview()` (rough HU stats) | ✅ **Extend debug endpoint** or create `lesion/analyzer.py` for separation of concerns | ✅ **New `lesion/analyzer.py`** (debug endpoint as entry point, analysis logic as independent module) |
| **RealismScorer** | ❌ No | — | No — no existing scoring logic | ✅ **New module** (may be part of `lesion/analyzer.py`) |

### 2.2 Redundancy Warnings

> ⚠️ **`LesionGenerator._generate_lesion_volume()` and `OrganSimulator._generate_organ_shape()` share ~60% similar code** (distance field, ellipsoid, mask generation). Future: extract common base class or mixin.

> ⚠️ **`phantom_generator.py` procedural CT generation overlaps with `volume_builder.py` synthetic volume** (both fill HU values into volumes). The former is more comprehensive (10+ anatomical structures), the latter is a single ellipsoid. Consider deprecating `build_synthetic_volume()` in favor of a lightweight call to `generate_upper_body_ct_phantom()`.

### 2.3 Principle

**No overlapping modules.** Every new module provides a clearly distinct capability not present in any existing location. Expand existing modules first; create new modules only when the gap cannot be closed by extension.

---

## Phase 3: Compatibility Analysis

### 3.1 Should LesionGenerator Remain the Unified Entry Point?

**Decision: YES — retain `LesionGenerator` as unified entry point. Do NOT split.**

Current `LesionGenerator` responsibility:
- Receive config dict → resolve parameters → invoke internal generation → return HU volume

Proposed extension pattern:
```
LesionGenerator.generate_lesion(config)
  ├── _generate_lesion_volume()           # Existing: voxel-based
  ├── _generate_mesh_lesion()             # New: Mesh-driven (delegates to MeshGenerator)
  └── apply_texture(lesion_vol)           # New: texture enhancement (delegates to TextureGenerator)
```

**Rationale for NOT splitting:**
- External callers (API `simulation.py`) already depend on `LesionGenerator` as single entry point
- Splitting would bloat API routing logic (`if voxel call A, if mesh call B`)
- Extending existing class is more maintainable than adding parallel classes

**However:** Mesh generation logic itself belongs in `lesion/mesh_generator.py`, invoked by `LesionGenerator` via delegation — this is **separation of concerns**, not **responsibility splitting**.

### 3.2 Should Lesion Writing Be Upgraded from Mask Replace to Blend?

**Decision: YES — upgrade from mask replace to blend writing.**

Current logic (identical in 3 places):
```python
lesion_mask = lesion_vol != 0
result_volume[lesion_mask] = lesion_vol[lesion_mask]
```

This is **hard replace** — lesion CT values completely overwrite underlying CT values.

**Two reasons to upgrade to blend:**
1. **Partial volume effect at tissue interfaces**: lesion boundary vs tissue boundary should do weighted blending
2. **Multi-lesion overlap**: hard replace causes last-writer-wins for overlapping lesions

Proposed pattern:
```python
blend = LesionBlender(
    base_volume=ct_volume,
    lesion_volumes=[lesion_1, lesion_2, ...],
    strategy="max_hu" | "weighted" | "first_wins",
)
result = blend.apply()
```

**Reusable capability**: `HUModifier.apply_operation()` pattern (volume + mask + operation) directly serves as blend foundation.

### 3.3 Can Existing Organ Mask / Placement Capability Be Reused?

**YES — the following can be directly reused:**

- `phantom_generator.ORGAN_LABEL_MAP` → 20-organ label definition
- `generate_atlas_ct_phantom()` → returns `(ct_volume, label_volume, metadata)` where `label_volume` is a full 3D organ mask
- `slice_label_presence` → per-organ z-axis presence range
- `OrganSimulator._get_organ_shape_params()` → synthetic organ geometry parameters

**Still missing (new modules needed):**
- No "find a valid placement position inside organ X" algorithm → `OrganAwarePlacement`
- No collision detection for "does this position overlap with existing lesions, major vessels, or airways?"
- `label_volume` from atlas is only used inside `phantom_generator`, not exposed as independent API

### 3.4 Does the Current vtk.js + Cornerstone3D Setup Satisfy 3D Rendering Requirements?

**YES — no new 3D engine is needed.**

Current coverage:
- Volume rendering (ray casting) → vtk.js VolumeRenderer
- Multi-planar reconstruction MPR → Cornerstone3D Viewport
- Segmentation mask visualization → vtk.js overlay + Cornerstone3D segmentation
- Lesion preview → vtk.js `syntheticData` property

**Architecture concern:** vtk.js currently receives data via `Float32Array` + `syntheticData` prop through base64-encoded NRRD. For large data volumes, shared memory or streaming should be considered. This is a **data transport path issue**, not an engine issue.

---

## Phase 4: Architecture Design — Lesion Engine V2

### 4.1 Module Summary

| Action | Module | Reason |
|--------|--------|--------|
| **Keep** | `exporter.py` | Mature export logic, 3 formats supported |
| **Keep** | `hu/modifier.py` | Stable HU operations, reusable by multiple consumers |
| **Keep** | `deformation/field.py` | B-spline + Jacobian, functionally complete |
| **Keep** | All `__init__.py` | Package declarations, no changes needed |
| **Keep** | `volume_builder.py` | DICOM pipeline and synthetic fallback are sufficient |
| **Modify** | `lesion/generator.py` | Extend as unified entry point (voxel + mesh + texture) |
| **Modify** | `api/v1/simulation.py` | Extract writing logic from API layer into blender module |
| **Modify** | `phantom_generator.py` | Expose organ labels as independent API |
| **Modify** | `organ/simulator.py` | Extend with placement mask capability |
| **New** | `lesion/mesh_generator.py` | Mesh→SDF→volume pipeline |
| **New** | `lesion/texture_generator.py` | Advanced texture (Perlin, fractal, organ-specific) |
| **New** | `lesion/analyzer.py` | Analysis + realism scoring (include RealismScorer) |
| **New** | `lesion/blender.py` | Unified blend writing (replace current 3 scattered writes) |
| **Delete** | None | No modules require removal |

### 4.2 Module Design Details

#### 4.2.1 `lesion/generator.py` — Extended Unified Entry

**Keep:**
- `LESION_HU_DEFAULTS` — 5 lesion type defaults
- `_generate_lesion_volume()` — distance field + sigmoid + HU noise
- `_apply_lobulation()`, `_apply_spiculation()`, `_apply_irregularity()` — shape deformation
- `generate_preview()` — 32³ preview
- Debug helpers (`_debug_save_lesion_mask_png`, `_debug_log_deformation`)

**Add:**
- `generate_lesion()` gains optional `mesh_path`, `apply_texture` parameters
- Delegates mesh path → `MeshGenerator.generate_from_mesh()`
- Delegates texture → `TextureGenerator.apply()`

```python
def generate_lesion(
    self,
    volume_shape: Tuple[int, int, int],
    config: Dict[str, Any],
    spacing: Optional[Tuple[float, float, float]] = None,
    mesh_path: Optional[str] = None,        # NEW
    apply_texture: bool = False,            # NEW
) -> np.ndarray:
```

【修改现有模块】

#### 4.2.2 `lesion/mesh_generator.py` — Mesh-Driven Lesion Generation (NEW)

```
Input:  .stl / .obj / .vtk triangular mesh + HU config
Process:
  1. Load mesh (trimesh / numpy-stl / vtk)
  2. Align mesh to target volume coordinate system
  3. Voxelize mesh → binary 3D mask
  4. Compute signed distance field (SDF)
  5. Apply soft margin (reuse sigmoid from generator.py)
  6. Assign HU values with noise (reuse strategy from generator.py)
Output: np.ndarray same shape/semantics as generator.py output
```

**Explicitly NOT a subclass of LesionGenerator:**
- Mesh pipeline involves different dependencies (trimesh, mesh IO)
- Different algorithmic structure (SDF vs distance field)
- Shared utility functions extracted as `_sigmoid_margin()`, `_hu_noise()` into a shared helper module

【新增模块】

#### 4.2.3 `lesion/texture_generator.py` — Texture Enhancement (NEW)

**Reusable capabilities from existing modules:**
- `HUModifier.add_noise()` → base noise layer
- `OrganSimulator._apply_enhancement()` → enhancement pattern logic
- `LesionGenerator` HU gaussian noise → internal heterogeneity

**New capabilities:**
- Perlin / fractal noise → natural texture
- Lesion-specific texture (tumor necrotic core vs cyst uniform fluid vs calcification speckles)
- Texture parameterization (frequency / amplitude / anisotropy)

```python
class TextureGenerator:
    def apply(
        self,
        lesion_volume: np.ndarray,
        texture_config: Dict[str, Any],
    ) -> np.ndarray:
        # 1. Base noise layer (reuse HUModifier)
        # 2. Perlin fractal noise (new)
        # 3. Lesion-type-specific modulation (new)
        # 4. Return enhanced volume
```

【新增模块】

#### 4.2.4 `lesion/blender.py` — Unified Blend Writing (NEW)

**Replace the current 3 scattered copies of:**
```python
result_volume[lesion_mask] = lesion_vol[lesion_mask]
```

**With unified:**
```python
class BlendStrategy(Enum):
    REPLACE = "replace"    # Current behavior: mask overwrite
    WEIGHTED = "weighted"  # Weighted blend (partial volume at boundary)
    MAX_HU = "max_hu"      # Take max per voxel (high-density lesions overlay)

class LesionBlender:
    def blend(
        self,
        base: np.ndarray,
        lesions: List[np.ndarray],
        spacing: Tuple[float, float, float],
        strategy: BlendStrategy,
    ) -> np.ndarray:
        # Handle multi-lesion stacking order
        # Apply boundary blending for soft tissue interfaces
        # Return final volume
```

**Reusable foundation:** `HUModifier.apply_operation()` pattern (volume + mask + op).

【新增模块】

#### 4.2.5 `lesion/analyzer.py` — Lesion Analysis + Realism Scoring (NEW)

**Reusable from existing:**
- `debug_lesion()` endpoint logic → HU stats, volume, diameter, position checks
- `generate_preview()` → summary statistics

**New capabilities:**
- Shape descriptors (sphericity, ellipticity, irregularity index)
- Boundary analysis (sharp vs diffuse margin score)
- Texture features (entropy, contrast, homogeneity via GLCM)
- Adjacency analysis (lesion vs organ relative position)
- Multi-lesion distance / overlap metrics
- **Realism scoring** (combined score from HU distribution match, shape naturalness, boundary plausibility, organ-appropriate location)

```python
class LesionAnalyzer:
    def analyze(
        self,
        base_volume: np.ndarray,
        result_volume: np.ndarray,
        lesion_mask: np.ndarray,
        organ_label_volume: Optional[np.ndarray] = None,
    ) -> Dict[str, Any]:
        # Returns: {hu_stats, shape_metrics, texture_features,
        #           boundary_score, placement_score, realism_score}
```

【新增模块】

#### 4.2.6 `api/v1/simulation.py` — API Updates

**Changes:**

1. **Extract writing logic** from all 3 endpoints into `LesionBlender`:
   - `run_simulation_job()` — replace inline `result_volume[lesion_mask] = lesion_vol[lesion_mask]`
   - `preview_lesion_on_dicom()` — same
   - `debug_lesion()` — same

2. **Add optional parameters** to `LesionConfigCreate`:
   - `mesh_path: Optional[str]`
   - `texture_config: Optional[Dict]`
   - `blend_strategy: Optional[str]`

3. **New endpoint**: `POST /api/v1/simulation/lesion/analyze`

4. **Extended responses**:
   - `DicomLesionPreviewResponse` adds: `realism_score`, `texture_stats`
   - `DebugLesionResponse` adds: Tasks 5-6

【修改现有模块】

#### 4.2.7 `phantom_generator.py` — Expose Organ Labels

**Add:**
```python
def get_atlas_label_volume(
    case_id: str,
    size: int = 192,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    """Return organ label volume without CT data — lightweight."""
```

This separates label volume loading from CT loading, letting `OrganAwarePlacement` query organ masks without loading CT data.

【修改现有模块】

#### 4.2.8 `organ/simulator.py` — Placement Mask

**Add:**
```python
def get_placement_mask(
    self,
    volume_shape: Tuple[int, int, int],
    organ_type: str,
    exclusion_radius_voxels: float = 0.0,
) -> np.ndarray:
    """Return binary mask of valid lesion placement positions within organ."""
```

Reuses `_get_organ_shape_params()` for geometry definition.

【修改现有模块】

---

## Data Flow

### V2 Data Flow Diagram

```
Frontend (React/TypeScript)
  │
  │  POST /api/v1/simulation/jobs
  │  { lesions: [{type, shape, mesh?, texture?, ...}], organs }
  ▼
FastAPI Simulation API (api/v1/simulation.py)
  │
  ├─── CT Volume Source ──────────────────────┐
  │   ├─ DICOM series → volume_builder.py     │
  │   ├─ Synthetic   → phantom_generator.py   │
  │   └─ Atlas       → phantom_generator.py   │
  │                                           │
  ├─── LesionGenerator.generate_lesion() ──────┤
  │   ├─ (voxel) → _generate_lesion_volume()  │  [保留]
  │   │   └─ dist field → sigmoid → HU noise  │
  │   └─ (mesh)  → MeshGenerator               │  [新增]
  │       └─ mesh → SDF → soft margin → HU     │
  │                                           │
  ├─── TextureGenerator.apply() ───────────────┤  [新增]
  │   └─ Perlin noise + lesion-type texture    │
  │                                           │
  ├─── OrganAwarePlacement ────────────────────┤  [新增]
  │   └─ atlas labels / organ params → pos    │
  │                                           │
  ├─── LesionBlender.blend() ─────────────────┤  [新增]
  │   ├─ replaced 3 scattered mask writes     │
  │   └─ supports strategy (replace/weighted) │
  │                                           │
  ├─── LesionAnalyzer.analyze() ──────────────┤  [新增]
  │   └─ HU stats + shape + texture + realism │
  │                                           │
  └─── Exporter ───────────────────────────────┘  [保留]
      ├─ NRRD  (direct)
      ├─ NIfTI (SimpleITK)
      └─ DICOM zip (pydicom)
```

### Key Data Flow Changes from V1

| Aspect | V1 | V2 |
|--------|----|----|
| Writing logic | Scattered across 3 endpoints (inline `mask[lesion_mask]=`) | Unified in `LesionBlender` |
| Mesh support | Not supported | `MeshGenerator` produces HU volume → consumed by `LesionBlender` |
| Texture | HU gaussian noise only | `TextureGenerator` adds Perlin + lesion-specific texture |
| Organ awareness | No placement logic | `OrganAwarePlacement` from atlas labels or organ params |
| Analysis | Debug endpoint (4 manual checks) | `LesionAnalyzer` with structured analysis + scoring |

---

## API Changes

### Modified Endpoints

| Endpoint | Change Type | Change Content |
|----------|-------------|----------------|
| `POST /api/v1/simulation/preview/lesion` | **Enhance** | Add optional `mesh_path`, `texture_enabled`, `blend_strategy` params |
| `POST /api/v1/simulation/preview/lesion-on-dicom` | **Enhance** | Same params; response adds `realism_score`, `texture_stats` |
| `POST /api/v1/simulation/debug-lesion` | **Enhance** | Add Task 5: texture analysis, Task 6: realism scoring |
| `POST /api/v1/simulation/preview/organ` | **Enhance** | Add optional `get_placement_mask` mode |
| `GET /api/v1/simulation/phantom` | **Enhance** | Add `?return_labels=true` to return label_volume separately |
| `POST /api/v1/simulation/jobs` | **Enhance** | `LesionConfigCreate` adds `mesh_path`, `texture_config`, `blend_strategy` |

### New Endpoint

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/v1/simulation/lesion/analyze` | `POST` | Independent analysis endpoint — returns structured analysis without generating new lesion |

### Schema Changes

```python
# LesionConfigCreate — enhanced
class LesionConfigCreate(BaseModel):
    # Existing fields (unchanged):
    lesion_type, shape, center_x/y/z, radius_x/y/z
    hu_mean, hu_std, margin_sharpness
    calcification_fraction, necrosis_fraction, spiculation_degree

    # NEW fields:
    mesh_path: Optional[str] = None          # Mesh file path for mesh mode
    texture_config: Optional[dict] = None    # Texture parameters
    blend_strategy: str = "replace"          # replace | weighted | max_hu
    organ_constraint: Optional[str] = None   # e.g. "liver", "kidney" — for organ-aware placement


# DicomLesionPreviewResponse — enhanced
class DicomLesionPreviewResponse(BaseModel):
    # Existing:
    image_base64, slice_index, total_slices, lesion_center_voxel
    hu_min, hu_max, hu_mean, hu_std, voxel_count, volume_mm3

    # NEW:
    realism_score: Optional[dict] = None     # {overall, hu_distribution, shape, margin, placement}
    texture_stats: Optional[dict] = None     # {entropy, contrast, homogeneity}


# DebugLesionResponse — enhanced (adds Task 5-6)
class DebugLesionResponse(BaseModel):
    # Existing Task 1-4 (unchanged):
    lesion_voxels, lesion_ratio, lesion_hu_mean/min/max/std
    changed_voxels, write_delta_mean/max
    volume_shape, center_voxel, bbox, inside_volume
    spacing, radius_mm, radius_voxel, z_compression_warning

    # NEW Task 5: Texture analysis
    texture_entropy: float = 0.0
    texture_contrast: float = 0.0
    texture_homogeneity: float = 0.0

    # NEW Task 6: Realism scoring
    realism_overall: float = 0.0
    realism_hu_dist: float = 0.0
    realism_shape: float = 0.0
    realism_margin: float = 0.0
    realism_placement: float = 0.0
```

---

## Database Changes

| Table / Model | Change Type | Change Content |
|---------------|-------------|----------------|
| `LesionConfig` | **Enhance** | Add fields: `mesh_path: Optional[str]`, `texture_config: Optional[JSON]`, `blend_strategy: Optional[str]`, `organ_constraint: Optional[str]` |
| `SimulationJob` | **No change** | Current schema already sufficient — job status/progress/lesions/organ tracking unchanged |
| **NEW** `TexturePreset` | **New table** | Name, parameters, applicable lesion types (pre-seeded or runtime-generated) |

### LesionConfig — Enhanced Fields

```python
class LesionConfig(Base):
    # Existing:
    id = Column(String, primary_key=True)
    job_id = Column(String, ForeignKey("simulation_jobs.id"))
    lesion_type = Column(String)       # tumor, nodule, cyst, calcification, metastasis
    shape = Column(String)             # spherical, lobulated, spiculated, irregular
    center_x = Column(Float)
    center_y = Column(Float)
    center_z = Column(Float)
    radius_x = Column(Float)
    radius_y = Column(Float)
    radius_z = Column(Float)
    hu_mean = Column(Float)
    hu_std = Column(Float)
    margin_sharpness = Column(Float)
    calcification_fraction = Column(Float)
    necrosis_fraction = Column(Float)
    spiculation_degree = Column(Float)

    # NEW:
    mesh_path = Column(String, nullable=True)                # Mesh file storage path
    texture_config = Column(JSON, nullable=True)             # Texture parameters
    blend_strategy = Column(String, default="replace")       # replace | weighted | max_hu
    organ_constraint = Column(String, nullable=True)          # e.g. "liver", "kidney"
```

---

## Migration Path

### Phase Order

| Phase | Content | Dependencies | Effort Estimate |
|-------|---------|-------------|-----------------|
| **P0** | Extract blend writing from API to `lesion/blender.py`; refactor 3 call sites | None (pure refactor) | Small (~2d) |
| **P1** | Create `lesion/analyzer.py`; add Tasks 5-6 to debug endpoint | None (can be parallel) | Small (~2d) |
| **P2** | Create `lesion/mesh_generator.py` | Need `trimesh` or equivalent dependency | Medium (~5d) |
| **P3** | Create `lesion/texture_generator.py` | Optional — can use existing `HUModifier` as fallback | Medium (~3d) |
| **P4** | `organ/simulator.py` placement mask; `phantom_generator.py` label expose | P0 + atlas data | Small (~2d) |
| **P5** | API schema updates; new endpoint; DB migration | P0-P4 | Small (~3d) |
| **P6** | Frontend integration (mesh upload UI, texture toggle, analysis display) | P0-P5 | Medium (~5d) |

**Total estimated effort: ~22 working days** (backend ~15d, frontend ~5d, integration ~2d).

P0-P1 can begin immediately as they are pure refactors of existing code with zero dependency risk.

---

## Appendices

### A. Module Responsibility Matrix

```
                       ┌─────────────────────────────────────────────────────────────┐
                       │                     CONSUMER                                 │
                       │  API Layer  │  LesionGen  │  OrganSim  │  Frontend  │  Exp  │
┌─────────────────────┼─────────────┼─────────────┼────────────┼────────────┼───────┤
│   PRODUCER          │             │             │            │            │       │
├─────────────────────┼─────────────┼─────────────┼────────────┼────────────┼───────┤
│ volume_builder      │  ✓          │             │            │            │       │
│ phantom_generator   │  ✓          │             │  ✓(atlas)  │  ✓(phantom)│      │
│ LesionGenerator     │  ✓          │             │            │  ✓(prev)   │       │
│ MeshGenerator    [N]│  (via gen)  │  ✓(delegate)│            │            │       │
│ TextureGenerator [N]│  (via gen)  │  ✓(delegate)│            │            │       │
│ LesionBlender    [N]│  ✓          │             │            │            │       │
│ LesionAnalyzer   [N]│  ✓          │             │            │  ✓(report) │       │
│ OrganSimulator      │  ✓          │             │            │  ✓(prev)   │       │
│ HUModifier          │             │  ✓(texture) │  ✓(enhance)│            │       │
│ DeformationField    │             │  ✓(shape)   │  ✓(defrm)  │            │       │
│ exporter            │  ✓          │             │            │            │       │
│ OrganAwarePlace [N] │  ✓          │  ✓(pos)     │            │            │       │
└─────────────────────┴─────────────┴─────────────┴────────────┴────────────┴───────┘
  [N] = New module in V2
```

### B. Key Design Decisions Summary

| # | Decision | Choice | Alternative Rejected |
|---|----------|--------|---------------------|
| D1 | LesionGenerator entry point | Unified (extend existing) | Split into VoxelGen + MeshGen |
| D2 | Writing mechanism | `LesionBlender` (new module) | Leave scattered in API |
| D3 | Mesh support | New `MeshGenerator` | Add to existing generator |
| D4 | Texture | New `TextureGenerator` | Leave bare HU noise |
| D5 | Organ awareness | Extend `organ/simulator.py` + `phantom_generator.py` | New `placement/` package |
| D6 | Analysis | New `lesion/analyzer.py` | Bloat debug endpoint |
| D7 | 3D engine | Keep vtk.js + Cornerstone3D | No new engine needed |
| D8 | Migration order | P0-P6, P0-P1 parallelizable | Big-bang replacement |
