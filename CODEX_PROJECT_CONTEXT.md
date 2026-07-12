# MedSim Studio Codex Project Context

## Update 2026-07-12 - CT params no longer collapse slice-synced 3D accumulation into a rectangle

This pass fixed the CT Simulation page issue where changing almost any CT
parameter could make the right-side 3D view appear as a long rectangle / thin
slab.

The user's clarified requirement was:

- the right 3D view is built from the same left 2D CT slice stack
- `Slice Sync` / progressive accumulation must remain the core behavior
- thickness, dose, mAs, kVp, pitch, FOV, matrix, kernel, and contrast changes
  must not destroy the anatomical completeness of the CT volume
- slice thickness should affect slice count / quality / partial-volume blur,
  not make the CT body incomplete
- a rectangular/expanded output box is only expected when gantry angle changes
  actually rotate/resample the volume

### Real root cause found

The main frontend bug was in `frontend/src/pages/SimulationPage.tsx`.

Before this fix, the active dataset key included
`ctParamsResult.simulatedVolumeBase64`. A `useEffect` watched that key and reset
`sliceIndex` to `scanStartIndex` whenever a new simulated CT result arrived.

Because the right 3D renderer displays progressive accumulation from the left
2D stack up to the current `sliceIndex`, every parameter run effectively reset
the 3D view back to the first informative slice. The result looked like a
rectangular slab even though the full simulated volume still existed.

### Frontend changes made

#### `frontend/src/pages/SimulationPage.tsx`

- replaced the result-dependent reset key with a phantom/workspace-only key:
  - old behavior: reset slice index whenever `ctParamsResult` changed
  - new behavior: reset slice index only when the loaded phantom/workspace
    changes
- CT parameter updates now preserve the current slice position and therefore
  preserve the intended 2D-to-3D progressive accumulation behavior
- for non-gantry parameter runs, the informative body slice range is based on
  the original loaded phantom when the simulated result shape still matches the
  phantom
- this prevents thickness/noise/intensity changes from redefining the visible
  anatomical scan range

#### `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

- adjusted CT transfer-function opacity so low-HU air/background is not rendered
  as an opaque box
- lung / soft-tissue / angio presets now keep air and low-HU background
  transparent before tissue-level opacity begins

### Backend changes made

#### `backend/app/simulation/ct_params_simulator.py`

- body support extraction now uses a stricter tissue threshold and fills
  per-axial-slice support holes before connected-component cleanup
- slice thickness simulation was changed to preserve volume coverage:
  - keep z/xy reconstruction scale at `1.0`
  - retain blur / slab averaging / partial-volume effects
  - remove the coarse downsample-and-upsample reconstruction step that could
    make thick slices look like they damaged CT completeness
- thickness model metadata is now:
  - `coverage_preserving_z_blur_plus_slab_averaging`

### Validation performed

- `python -m py_compile backend/app/simulation/ct_params_simulator.py` passed
- `npx tsc --noEmit` passed in `frontend`
- Docker frontend/backend were rebuilt/recreated during validation
- running backend container was checked and contained the current simulator
  changes
- running frontend bundle was checked and contained the new
  `phantomDatasetKey` slice-reset logic
- the user confirmed the issue was solved after the final fix

### Repository state

This pass was committed and pushed.

- commit: `46bac12`
- message: `fix: preserve ct slice accumulation during parameter updates`
- branch: `main`
- remote: `origin/main`

### Important notes for the next conversation

- do not reintroduce `ctParamsResult.simulatedVolumeBase64` into the slice-reset
  effect dependency path
- right-side 3D must continue to be derived from the same active CT stack as the
  left-side 2D slice view
- non-angle CT parameter changes should preserve body geometry and slice-sync
  accumulation
- only gantry pitch/yaw/roll changes should be expected to alter the output
  bounding box / rotated rectangular extent
- if a similar rectangle issue returns, first inspect:
  1. whether `sliceIndex` is being reset after parameter runs
  2. whether progressive scalar filling is receiving the expected
     `syntheticClipIndex`
  3. whether transfer functions are making air/background visible
  4. whether the frontend Docker image was rebuilt and the browser is loading
     the new bundle

## Update 2026-07-11 - organ colors darkened and separation increased for CT overlays

This pass happened after the user reviewed the latest lighter organ overlay
palette and asked for the colors to become deeper again, with stronger visual
separation between organs.

The user requirement in this pass was specifically:

- keep organ colors visible
- make the colors darker than the previous softened pastel version
- increase inter-organ distinction so adjacent structures are easier to tell
  apart
- avoid returning to an overly opaque overlay that hides CT detail

### Frontend changes made

#### `frontend/src/pages/SimulationPage.tsx`

- the organ color table was retuned from a light pastel palette to a deeper,
  more distinct palette
- left/right kidneys and lung lobes now use more separated hues instead of
  nearly identical colors
- `LABEL_OVERLAY_ALPHA` was set to `0.28`
  - this makes 2D organ overlays more visible than the lighter pass
  - but still avoids the earlier heavier look that obscured CT texture
- fallback colors for non-predefined labels were changed to:
  - higher saturation
  - lower lightness
  - stronger label-to-label separation
- the shared color softening step was retained, but reduced significantly:
  - previous mix was stronger
  - current `softenOrganColor(...)` mix is `0.06`

#### `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

- segmentation overlay opacity was raised to better match the darker palette:
  - `DEFAULT_SEG_OPACITY = 0.11`
  - `SELECTED_OPACITY_BOOST = 0.10`
- organ surface material was kept relatively soft so deeper colors do not look
  too glossy or muddy in 3D

### Result

- organ colors are now darker than the previous pass
- the visual distinction between neighboring organs is stronger
- 2D and 3D segmentation overlays are easier to read without becoming fully
  heavy or opaque

### Validation

- `npx tsc --noEmit` passed in `frontend`

## Update 2026-07-11 - DICOM multi-organ colors restored under Slice Sync via label-volume alignment and slice-synced segmentation volume rendering

This pass happened after the user continued reporting that organ colors still
did not load in the CT simulation page, even though the backend appeared to
have nnUNet weights capable of segmenting 20 organs.

The user also gave one hard requirement that constrained the fix:

- `Slice Sync` must remain enabled and usable
- the 3D view must behave like progressive CT loading from the top and build
  up the 3D body as slices advance

### What was actually confirmed

The issue was not "backend has no segmentation" and not simply "colors are
missing from the legend".

Confirmed during this pass:

- the backend was returning DICOM workspace segmentation successfully
- direct API validation against the running backend returned:
  - `label_source = nnunet`
  - `label_model_name = nnunet702_20organs`
  - `label_base64` present
  - `label_map` populated for labels `1..20`
  - non-zero labels present in the returned mask
- the frontend diagnostics later shown by the user also confirmed:
  - `Labels: on`
  - `Source: nnunet`
  - `Model: nnunet702_20organs`
  - `Nonzero labels: 5`

Important implication:

- segmentation data was already arriving at the frontend
- the failure was in frontend rendering behavior, not label generation itself

### Real root causes found in the frontend

Two separate frontend issues were identified.

#### 1. Segmentation mask voxel order did not match the CT vtk volume order

The CT volume data was transposed before being passed into vtk, but the
segmentation and lesion masks were still being passed through in raw zyx order.

That meant:

- the CT and label volume were not using the same memory layout inside vtk
- organ overlays could appear misplaced, clipped incorrectly, or effectively
  invisible

#### 2. Surface-mesh organ rendering was a poor fit for progressive Slice Sync

The current 3D segmentation renderer was based on per-organ extracted surfaces.
That works better when the full volume is already available, but under
`Slice Sync` the progressive clipping logic meant organs could remain hard to
see or seem absent during early accumulation.

The user explicitly wanted the progressive scan effect preserved, so disabling
`Slice Sync` was not an acceptable solution.

### Frontend changes made

#### `frontend/src/pages/SimulationPage.tsx`

- added `transposeZyxMaskToVtkOrder(...)`
- organ segmentation masks are now transposed into the same vtk voxel order as
  the CT volume before rendering
- lesion masks are also transposed the same way for consistency
- DICOM diagnostics were added to the page so the loaded workspace now shows:
  - `Labels`
  - `Source`
  - `Model`
  - `Nonzero labels`
- when studies load and no study is selected, the first study is auto-selected
- when CT workspace series load and none is selected, the first CT series is
  auto-selected
- DICOM loading behavior was tightened:
  - if a study is selected, the load source is treated as `dicom`
  - if a study is selected but no CT series is selected, loading is blocked
    with an explicit error instead of silently falling back to atlas
- `Slice Sync` default remains enabled:
  - `const [sync3DToSlice, setSync3DToSlice] = useState(true);`

#### `frontend/src/services/simulationService.ts`

- `PhantomMetadata` was extended so the frontend can carry DICOM label
  diagnostics:
  - `labelSource`
  - `labelModelName`
  - `labelError`
  - `labelsEnabled`
  - `segmentationSeriesId`

#### `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

The renderer now supports two segmentation rendering paths:

1. normal full-volume mode:
   - keep the existing per-organ smoothed surface actors
2. `Slice Sync` progressive mode:
   - render segmentation as a clipped label volume overlay instead of only
     extracted surfaces

Implementation details:

- added `SegmentationVolumeRef`
- added cleanup for segmentation volume resources
- clipping planes are now applied to:
  - CT mapper
  - segmentation volume mapper when present
  - organ surface mappers
- in `Slice Sync` mode (`syntheticClipIndex >= 0`), the renderer now:
  - builds a segmentation `vtkImageData`
  - creates a `vtkVolumeMapper`
  - assigns per-label color transfer function entries
  - assigns per-label opacity bands around each integer label value
  - uses nearest-neighbor interpolation so labels stay discrete
  - overlays the segmentation volume progressively as slices accumulate
- outside `Slice Sync` mode, the original per-organ surface rendering path is
  retained
- live opacity / visibility updates now work for both:
  - segmentation volume overlay mode
  - per-organ surface mode

### Why this fixed the visible behavior

The final fix was not just "add more colors".

It changed the rendering model so that:

- label voxels align spatially with the CT volume
- progressive scan accumulation can reveal segmentation as the scan advances
- colored organ regions can appear during slice-synced buildup instead of
  waiting for stable full organ surfaces

### Validation performed

- `npx tsc --noEmit` passed in `frontend`
- backend API validation confirmed nnUNet multi-organ labels were being
  returned for the current DICOM workspace test study
- the user later reported that segmented organs were appearing in greater
  quantity than before, which was the expected direction after this fix

### Current repository state after this pass

This pass was committed.

- commit: `5ba693a`
- message: `fix: improve slice-synced dicom segmentation rendering`

### Important notes for the next conversation

- do not remove or disable `Slice Sync` to "fix" segmentation visibility; the
  user explicitly needs the progressive CT-style generation behavior
- if the user reports that "too many" organs appear, that is now more likely a
  segmentation quality / model-selection question rather than the old
  color-loading failure
- the first things to inspect next are:
  1. label alignment and voxel order assumptions
  2. whether the renderer is in slice-synced volume-overlay mode or full
     surface mode
  3. backend `label_source`, `label_model_name`, and `label_nonzero_counts`
  4. whether the current DICOM CT field of view actually contains the organs
     the user expects

## Update 2026-07-11 - move 3D organ visibility checklist below the volume

This pass addressed a layout issue reported by the user on the CT simulation
page: the organ visibility checklist inside the `3D Volume` panel visually
competed with and partially covered the 3D rendering area.

### What changed

- the organ visibility checklist was moved out of the internal
  `VolumeRenderer` overlay area
- the checklist is now rendered in the page layout immediately before
  `CT Scan Params Simulation`
- the compact label color legend was also kept in that lower page region
  instead of remaining farther down the page

### Implementation details

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`
  - added optional controlled props for:
    - hidden segmentation labels
    - selected segmentation label
    - external toggle / selection handlers
    - whether the built-in segmentation checklist should be shown
  - the renderer can now either manage its own segmentation-control state or
    defer to page-level state
- `frontend/src/pages/SimulationPage.tsx`
  - added page-level state for hidden / selected organ labels
  - passed that state into `VolumeRenderer` so the external checklist controls
    the same 3D organ actors
  - added a new `3D Organ Visibility` section immediately above
    `CT Scan Params Simulation`
  - reset external organ selection / visibility state when the loaded label
    dataset changes

### Result

- the 3D rendering card no longer contains the organ checkbox list
- organ visibility controls remain functional and synchronized with the 3D
  organ surfaces
- the user can manage organ visibility below the main image workspace without
  obscuring the 3D view

### Validation

- `npx tsc --noEmit` passed in `frontend`

## Update 2026-07-11 - repository reset to origin/main after unsuccessful CT 2D recovery attempt

This update records the final state after a later attempt to repair the left
CT 2D workspace image loading did not produce an acceptable result for the
user.

### What happened in this pass

- the left-side CT 2D loading issue was investigated again
- several local frontend/backend adjustments were tried during the session
- despite those attempts, the user reported that the left 2D CT image still
  did not load correctly in the desired way
- the user then chose to abandon that experimental local state and requested a
  return to the remote `main`

### Important final repository state

- the local repository was reset back to `origin/main`
- current branch: `main`
- current commit after reset:
  - `b325cea` `fix: improve CT workspace 2D image clarity`
- working tree is clean after the reset

### Important implication for the next conversation

- any uncommitted local edits from the failed recovery attempt were discarded
- those discarded edits are **not** part of the current codebase and should not
  be assumed to exist
- the effective current baseline for further work is exactly the remote
  `origin/main` state at commit `b325cea`

### Most recent retained commits after the reset

1. `b325cea` `fix: improve CT workspace 2D image clarity`
2. `e0d3411` `chore: checkpoint current project state`
3. `1ee8c6b` `Restore organ label surface colors`
4. `4b6e102` `Show synchronized torso slice guide`
5. `1e9753c` `Add torso slice position guide`
6. `72a557f` `Restore LUNG1 labels and refine CT preview`
7. `17bb385` `Fix CT scan accumulation direction`
8. `3d3a1ce` `Unify LUNG1 workspace flow and refine CT visualization`

## Update 2026-07-11 - DICOM multi-organ label debugging follow-up and 3D front-view default

This follow-up pass happened after the user reported that the simulation page
still only showed lungs, and separately asked that the default 3D body view
should face the user instead of showing the patient from the back.

### 1. Real root cause for "only lungs visible" on DICOM

The key issue is not simply the frontend legend or 3D renderer.

What was confirmed:

- the current DICOM workspace data can legitimately be chest-focused CT
- same-study DICOM SEG content in these cases may only contain:
  - left lung
  - right lung
  - spinal cord
  - neoplasm
- therefore, if the backend prefers same-study SEG before running a broader
  multi-organ model, the page will continue to show mostly lungs even if local
  nnUNet weights exist

Important implication:

- if the CT scan itself does not include abdominal organs in the actual field
  of view, no model can invent full liver / spleen / kidneys / pancreas labels
  outside the scanned anatomy

### 2. Backend DICOM label loading was changed to prefer nnUNet before SEG

To better match the user's expectation that uploaded model weights should be
used for DICOM workspace labeling, the DICOM workspace label path in
`backend/app/api/v1/simulation.py` was updated so that:

1. load the DICOM CT volume
2. if labels are enabled, try local nnUNet first
3. if nnUNet returns no valid label volume, fall back to same-study DICOM SEG
4. if neither path succeeds, still return the CT volume and keep label metadata
   empty / diagnostic

Current nnUNet priority order for DICOM workspace labels:

1. `nnunet702_20organs`
2. `nnunet_lung_lobe`
3. `nnunet_handoff`

This is different from the earlier pass that preferred DICOM SEG before nnUNet.

### 3. Workspace load now supports explicit "include labels" control

The user also wanted the simulation page to choose whether organ labels should
be loaded at all.

Changes made:

- `backend/app/api/v1/simulation.py`
  - `/api/v1/simulation/phantom` now accepts `include_labels`
  - when `include_labels=false`, the response suppresses:
    - `label_base64`
    - `label_map`
    - `label_nonzero_counts`
    - `slice_label_presence`
    - `label_source`
    - `label_model_name`
    - `segmentation_series_id`
  - response metadata now includes:
    - `labels_enabled`
- `frontend/src/services/simulationService.ts`
  - `getPhantom(...)` now forwards `include_labels`
- `frontend/src/pages/SimulationPage.tsx`
  - the CT workspace toolbar now includes:
    - `Include organ labels`

### 4. Frontend no longer drops non-lung labels due to hardcoded color filtering

Another real issue found in the frontend was that organ labels were still being
filtered through a hardcoded color map, so any backend label without an
explicit predefined color could be silently dropped from:

- 2D axial overlay
- 3D segmentation overlay
- bottom legend

Fix:

- `frontend/src/pages/SimulationPage.tsx`
  - label filtering no longer requires `ORGAN_COLORS[index]`
  - visible label ordering still keeps priority labels first, but now appends
    all other recognized labels
  - dynamic fallback colors are generated for unknown label IDs
  - 2D slice rendering now uses backend-derived / computed label colors instead
    of assuming only the older hardcoded label set

### 5. Important runtime finding about model availability checks

An explicit local Python check from the host environment raised:

- `FileNotFoundError: /app/models/nnunet702_handoff`

This does **not** prove the Docker backend is broken.

What it means:

- the model wrappers resolve paths like `/app/models/...`
- those paths only exist inside the backend container
- host-side direct Python imports are therefore not a reliable proof that the
  containerized runtime cannot see the weights

What was also confirmed locally:

- the project directory does contain these model folders:
  - `models/nnunet702_handoff`
  - `models/nnunet_lung_lobe`
  - `models/nnunet701_full_handoff`
- `docker-compose.yml` mounts them into the backend container at:
  - `/app/models/nnunet702_handoff`
  - `/app/models/nnunet_lung_lobe`
  - `/app/models/nnunet_handoff`

So the remaining practical question is container runtime validation, not merely
filesystem presence in the repository.

### 6. Default 3D body view now starts from the front

The user reported that the initial 3D body orientation showed the patient from
the back and asked to change it so the patient faces the viewer by default.

Change made:

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`
  - `resetCameraToVolume(...)` now places the default camera on the opposite
    Y side of the volume
  - this changes the initial body view from posterior-facing to anterior-facing
    without altering the slice-sync clipping direction logic

### 7. Validation performed in this pass

Static checks run:

- `python -m py_compile backend/app/api/v1/simulation.py`
- `npx tsc --noEmit` in `frontend`

Additional manual inspection / confirmation:

- local model folders exist under `models/`
- compose mounts for nnUNet model directories are present in `docker-compose.yml`

### 8. Current state for the next conversation

- The frontend now has an explicit `Include organ labels` toggle for CT
  workspace loading.
- The frontend should no longer hide non-lung labels merely because they were
  absent from the old hardcoded color table.
- The DICOM backend label path now prefers multi-organ nnUNet before same-study
  DICOM SEG.
- If the user still only sees lungs after rebuilding / restarting the backend,
  the next check should be:
  1. whether the backend container is actually running the new code
  2. whether `metadata.label_source` is `nnunet` or `dicom_seg`
  3. whether the CT series itself is chest-limited and does not include other
     organs in the scanned range
- The default 3D body orientation should now start with the patient facing the
  viewer.

## Update 2026-07-11 - DICOM workspace labels, nnUNet fallback, and source UI simplification

This pass addressed the user's follow-up that the simulation workspace should
not require manually switching the lower-left source selector to `atlas` just to
see organ labels. The intended behavior is:

- keep atlas loading available
- remove the visible workspace `Source` selector from the compact toolbar
- let DICOM folder loading produce organ labels when possible
- show every organ recognized by the local segmentation model, not only lungs

### Frontend Workspace Source UI

- `frontend/src/pages/SimulationPage.tsx`
  - removed the visible compact-toolbar `Source: atlas / procedural / dicom`
    selector
  - kept atlas behavior internally as a fallback when no DICOM CT series is
    selected
  - always shows DICOM `Study` and `Series` controls in the compact toolbar
  - selecting a DICOM study or series marks the workspace source as `dicom`
  - `Load CT` now chooses DICOM when a CT series is selected, otherwise falls
    back to atlas
  - atlas functionality remains available through the internal
    `selectedAtlasCaseId` fallback path

### DICOM SEG Support

- `backend/app/api/v1/simulation.py`
  - DICOM workspace loading now searches same-study series for DICOM SEG data
  - SEG frames are converted into the workspace `uint8` label volume when their
    segment labels match the current LUNG1-compatible mapping
  - SEG labels are aligned to CT slices through `ImagePositionPatient`
  - label z-order is flipped and nearest-neighbor-resampled with the CT volume
  - response metadata now includes:
    - `segmentation_series_id`
    - `label_source`
    - `label_model_name`
    - `label_error`
    - `label_map`
    - `label_nonzero_counts`
    - `slice_label_presence`

### nnUNet Fallback For Uploaded DICOM Folders

The user noted that local nnUNet weights had already been uploaded and should be
used to segment imported DICOM folders. The DICOM workspace load path now works
in this order:

1. load the DICOM CT volume
2. use same-study DICOM SEG if present
3. if no SEG label volume is available, run local nnUNet automatically
4. if nnUNet fails, still return the CT volume and record the error in metadata

The fallback model priority is currently:

1. `nnunet702_20organs`
2. `nnunet_lung_lobe`
3. `nnunet_handoff`

Runtime validation inside Docker showed all three model wrappers available:

- `nnunet20 = True`
- `lung_lobe = True`
- `nnunet6 = True`
- CUDA is visible, but current `AI_DEVICE` resolved to `cpu` in the container

Validation with the current local DICOM test study, which contains CT only and
no SEG series, returned:

- `label_source = nnunet`
- `label_model_name = nnunet702_20organs`
- `label_base64 = true`
- non-zero labels included `3`, `10`, `12`, `13`, `16`, `17`, and `18`

### Show All Recognized Organs

The backend was returning multiple organ labels, but the frontend 3D overlay
only passed labels listed in `ORGAN_LABEL_PRIORITY`; labels outside that
priority list could be dropped from the renderer even though the legend handled
them.

- `frontend/src/pages/SimulationPage.tsx`
  - 3D organ overlay label selection now mirrors the legend logic
  - it reads every label in backend `labelMap`
  - it filters out only labels with known zero voxel counts
  - it orders priority labels first, then appends all other recognized labels
  - `VolumeRenderer` already creates isolated surface actors per label, so no
    renderer change was required for the "all organs" behavior

### Validation Run

Commands / checks run during this pass:

- `python -m py_compile backend/app/api/v1/simulation.py`
- `npx tsc --noEmit` in `frontend`
- `docker compose up -d --build backend`
- `docker compose up -d --build frontend`
- direct API validation:
  - `GET /api/v1/simulation/phantom?source=dicom&size=64&study_id=...&series_id=...&scan_direction=head_to_feet`
  - returned `label_source = nnunet`
  - returned `label_model_name = nnunet702_20organs`
  - returned `label_base64 = true`

### Current Operational Notes

- Frontend is available at `http://localhost:5173`
- Backend is available at `http://localhost:8000`
- The first DICOM load can take noticeably longer because nnUNet inference is
  now part of the load path when no DICOM SEG is present
- If the user wants faster interactive loading, the next improvement should be
  caching nnUNet workspace labels by study/series/size/scan direction or moving
  fallback segmentation into a background job with progressive UI state

## Update 2026-07-11 — fully isolated 3D organ segmentation layers

The 3D segmentation renderer was revised so segmentation can no longer alter
the appearance of the original CT volume or any voxel outside an organ mask.

### Implementation

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`
  - keeps the CT `vtkVolume`, mapper, transfer functions, opacity function, and
    material as the unchanged base rendering layer
  - restores the pre-segmentation CT soft-tissue and lung transfer-function
    presets, volume material values, sample distance, opacity unit distance,
    default renderer lighting, full-volume camera framing, and anterior camera
    position
  - creates one binary `vtkImageData` mask per non-zero segmentation label;
    label `0` and every voxel belonging to another label stay absent from that
    organ's pipeline
  - runs `vtkImageMarchingCubes` at iso-value `0.5` for every organ separately
  - applies a conservative `vtkWindowedSincPolyDataFilter` pass (8 iterations,
    pass band `0.2`, feature-edge smoothing off, boundary smoothing off), then
    recomputes point normals with `vtkPolyDataNormals`
  - assigns each organ its backend label color and a soft semi-transparent Phong
    material; no bounding-box actor, slice-plane actor, or regional color volume
    is created
  - stores each organ pipeline/actor in a map keyed by label ID, so visibility,
    selection highlight, color, and opacity updates change only that actor
  - adds per-organ visibility checkboxes and click-to-highlight controls; these
    operations do not rebuild or mutate the CT actor or other organ actors
  - applies clipping only as mapper geometry clipping to the CT and organ actors;
    it does not color the clipping plane or the clipped region
- `frontend/src/pages/SimulationPage.tsx`
  - restores the original default `Soft` window selection
  - continues passing the integer segmentation label map unchanged; the renderer
    is responsible for isolating each label into its own surface layer
- `frontend/src/types/vtk-filters.d.ts`
  - declares the vtk.js Marching Cubes, windowed-sinc smoothing, and normals
    filter modules used by the surface pipeline

### Verification

- `npx tsc --noEmit` passes in `frontend`
- `npm run build` passes; Vite resolves and bundles all three vtk.js surface
  filters successfully
- `npm test -- --runInBand` starts Vitest successfully, but this repository
  currently contains no matching test files (`No test files found`)
- reviewed the segmentation update path to confirm it never calls the CT
  actor's `setRGBTransferFunction`, `setScalarOpacity`, or material setters
- reviewed mask construction to confirm only exact label matches become `1`;
  background, bounding boxes, slice planes, and unrelated anatomy remain `0`

## Update 2026-07-11

This section also captures the later 2026-07-11 follow-up pass focused on improving the right-side 3D lung segmentation presentation.

### Additional 3D lung rendering follow-up on 2026-07-11

#### 1. Right-side lung segmentation was changed from voxel volume overlay to extracted surface mesh

- the previous overlay rendered the segmentation label volume directly as translucent voxels
- this caused obvious staircase edges, blocky surfaces, and noisy front/back overlap
- the frontend renderer now:
  - isolates lung-related segmentation labels only
  - unions them into a binary lung mask
  - runs `vtkImageMarchingCubes` at iso-value `0.5`
  - applies light `vtkWindowedSincPolyDataFilter` smoothing
  - recomputes normals with `vtkPolyDataNormals`
- the practical result is a cleaner lung surface that keeps anatomical shape without the previous voxel-mask jaggedness

Relevant file:

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 2. Lung material, lighting, and depth cues were redesigned

- the new lung surface uses a pale light-blue semi-transparent material
- default lung opacity is now about `0.54`
- the renderer now adds a small three-light setup:
  - key light
  - cool fill light
  - subtle rim light
- the lung mesh now uses diffuse shading plus a restrained specular highlight to improve curvature and depth separation without looking plastic

Relevant file:

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 3. CT body rendering was pushed into the background

- `ct-soft-tissue` and `ct-lung` 3D transfer functions were reduced in brightness and opacity
- volume material parameters were softened so outer body surfaces, spine, and skin do not dominate the frame
- sample distance and opacity unit distance were also tuned so the CT reads more as contextual anatomy and less as an opaque blocker in front of the lungs

Relevant file:

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 4. Camera framing now focuses on the lungs

- when lung segmentation exists, the 3D camera now frames lung bounds instead of the full CT bounds
- the camera uses a mild oblique anterior view and extra margin so both lungs remain centered and fully visible
- this reduces unrelated surrounding anatomy and improves front/back depth readability

Relevant file:

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 5. Default viewing preset now starts in lung windowing

- the simulation page default window preset was changed from `Soft` to `Lung`
- this better matches the lung-focused 3D rendering on initial load

Relevant file:

- `frontend/src/pages/SimulationPage.tsx`

This section captures the latest follow-up work from 2026-07-11, after the user reported two regressions:

- the 3D slice-sync direction had become worse and appeared to scan from bottom to top
- the organ / segmentation labels that previously appeared under the image and in 2D/3D overlays disappeared

### What Was Fixed In This Pass

#### 1. Slice-sync direction regression was reverted

- a previous attempted fix changed the 3D progressive clip direction to `high_to_low`
- the user confirmed this made the order visibly wrong: bottom-to-top
- the frontend was changed back to:
  - `syntheticClipDirection="low_to_high"`
- this restores the scan direction behavior to the prior expected direction

Relevant file:

- `frontend/src/pages/SimulationPage.tsx`

#### 2. Docker backend now mounts real LUNG1 sample data

Root cause found:

- `data/lung_lobe_samples` contains the real local `LUNG1-*` sample DICOM data:
  - `LUNG1-001`
  - `LUNG1-002`
  - `LUNG1-003`
- Docker backend previously mounted `models/phantom_atlas`, but not `data/lung_lobe_samples`
- therefore the running backend container could not reliably see the real local `LUNG1` sample data
- the frontend had a hardcoded `LUNG1-001` fallback option, so the UI could appear to offer LUNG1 even when the backend runtime did not have the actual sample directory mounted

Fix:

- added a read-only bind mount:
  - `./data/lung_lobe_samples:/app/data/lung_lobe_samples:ro`
- added backend validation:
  - if `case_id` starts with `LUNG1-` but the sample case is not present under `data/lung_lobe_samples`, the backend now raises a clear `FileNotFoundError`

Relevant files:

- `docker-compose.yml`
- `backend/app/simulation/phantom_generator.py`

Validation:

- `docker compose config` showed the new `/app/data/lung_lobe_samples` read-only mount
- backend was rebuilt and restarted with `docker compose up -d --build backend`
- direct API request confirmed real LUNG1 loading:
  - endpoint: `GET /api/v1/simulation/phantom?source=atlas&size=192&case_id=LUNG1-001&scan_direction=head_to_feet`
  - `case_id = LUNG1-001`
  - original shape: `134x512x512`
  - output shape: `50x192x192`
  - `flipped_z = True`
  - `scan_direction = head_to_feet`
  - `spatial_reference = dicom_patient_space`
  - loaded DICOM series: `0.000000-NA-82046`

#### 3. LUNG1 DICOM SEG labels are now converted into workspace label overlays

Root cause found:

- the LUNG1 backend branch loaded only the CT image volume
- it returned:
  - `label_volume = None`
  - `label_map = {}`
  - `label_nonzero_counts = {}`
  - `slice_label_presence = {}`
- therefore the frontend had no `label_base64` for:
  - 2D axial label overlay
  - 3D segmentation color overlay
  - bottom label legend

Fix:

- the LUNG1 loader now reads the bundled DICOM SEG object under the `Segmentation` series
- it converts SEG frames into a z/y/x `uint8` label volume aligned to the CT volume via `ImagePositionPatient`
- it applies the same z-flip and nearest-neighbor resampling as the CT output path
- the converted label volume is returned as `label_base64`
- label metadata is populated for frontend display

Current LUNG1 SEG mapping:

- DICOM SEG segment `Neoplasm, Primary GTV-1` -> label `100` (`neoplasm_primary_gtv`)
- DICOM SEG segment `Lung Lung-Left` -> label `13` (`left_lung_upper_lobe` reused as left lung display id)
- DICOM SEG segment `Lung Lung-Right` -> label `14` (`right_lung_upper_lobe` reused as right lung display id)
- DICOM SEG segment `Spinal cord Spinal-Cord` -> label `21` (`spinal_cord`)

Frontend changes:

- added colors for label `21` and `100`
- label display priority now includes `100`, `13`, `14`, and `21` first
- bottom `Labels:` legend is no longer hardcoded to atlas abdominal organs only
- it now derives visible label items from backend `label_map` and `label_nonzero_counts`

Relevant files:

- `backend/app/simulation/phantom_generator.py`
- `frontend/src/pages/SimulationPage.tsx`

Validation:

- container could decode the LUNG1 DICOM SEG pixel data:
  - shape: `(536, 512, 512)`
  - dtype: `uint8`
  - max value: `1`
  - nonzero sum: `1903588`
- backend API through frontend proxy confirmed restored label payload:
  - `label_base64 = True`
  - `label_base64` length: `2457600`
  - label counts:
    - `13`: `40776`
    - `14`: `53255`
    - `21`: `792`
    - `100`: `2801`
  - label z-ranges:
    - `lung`: `[8, 40]`
    - `lung_left`: `[8, 40]`
    - `lung_right`: `[10, 40]`
    - `spinal_cord`: `[9, 39]`
    - `neoplasm`: `[18, 25]`
- static checks passed:
  - `python -m py_compile backend/app/simulation/phantom_generator.py`
  - `npx tsc --noEmit`
- backend and frontend were rebuilt/restarted:
  - `docker compose up -d --build backend`
  - `docker compose up -d --build frontend`
- `docker compose ps` showed:
  - backend healthy
  - frontend healthy
  - postgres healthy
  - minio healthy

### Important Current State For The Next Conversation

- The immediate label regression should be fixed: LUNG1 now returns `label_base64`, and frontend 2D/3D overlays have label data again.
- The frontend served at `http://localhost:5173` has been rebuilt after these changes.
- The current direction regression from the attempted `high_to_low` clip change was reverted to `low_to_high`.
- The broader user complaint about the perceived LUNG1 anatomical direction / visible range may still need visual browser validation and potentially a separate camera/orientation/display-range pass.
- Do not reintroduce `syntheticClipDirection="high_to_low"` unless the visual behavior is verified in the browser.
- If label overlays disappear again, first check:
  - backend response contains `label_base64`
  - backend `metadata.label_nonzero_counts` is non-empty
  - frontend `showLabelOverlay` is true
  - frontend `organSegmentationOverlay` has `labels.length > 0`

### Current Dirty Worktree Note

The working tree contains multiple uncommitted files. Some were already dirty from earlier CT parameter simulation and visualization work. Files touched or relevant in the latest 2026-07-11 follow-up include:

- `docker-compose.yml`
- `backend/app/simulation/phantom_generator.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

Other dirty files from earlier work may include:

- `backend/app/api/v1/simulation.py`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `frontend/src/types/simulation.ts`
- `CODEX_PROJECT_CONTEXT.md`

## Update 2026-07-10

This section captures the latest `LUNG1` integration and CT workspace / visualization changes completed on 2026-07-10, including the follow-up scan-order fixes made later the same day.

### Additional follow-up on 2026-07-10 (later same day)

After the earlier `LUNG1` visualization and CT workspace changes, additional follow-up work was completed on the CT parameter preview path.

What was added / changed in this later pass:

#### 6. Simulated label volume now follows CT parameter preview output

- `ct-params/preview` previously returned only the simulated CT volume
- this caused the left axial overlay and right 3D overlay to fall out of sync after CT parameter simulation
- the backend now returns `simulated_label_base64` when a label volume exists
- the frontend now decodes and uses the simulated label volume for:
  - left-side axial overlay after CT parameter simulation
  - right-side 3D segmentation color overlay after CT parameter simulation
- the right-side 3D progressive accumulation now clips both:
  - the CT volume
  - the segmentation overlay volume
  so the overlay accumulates with the slices instead of appearing as an unsynchronised static layer

Relevant files:

- `backend/app/api/v1/simulation.py`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/types/simulation.ts`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 7. 3D accumulated CT view was tuned to look more like slice-by-slice buildup

- when `Slice Sync` is active, the right-side 3D view now forces a more scan-like soft-tissue rendering path instead of inheriting a more shell-like / bone-like volume look
- the accumulated CT volume now uses a less specular, less shiny rendering configuration in scan mode
- segmentation overlay default opacity was slightly increased from the earlier very light setting so the accumulated color layer reads more clearly without replacing the CT body
- the practical goal of this pass was:
  - original CT remains the base body
  - segmentation remains only a light color layer
  - the accumulation should visually read as layered CT slices rather than a static opaque organ shell

Relevant files:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 8. Slice-thickness simulation was expanded and made much more visible

- the available slice thickness presets were expanded beyond the earlier max of `10.0 mm`
- the UI and schema now support:
  - `0.625`
  - `1.0`
  - `2.5`
  - `5.0`
  - `10.0`
  - `15.0`
  - `20.0`
- slice-thickness simulation is no longer only mild z-blur
- it now combines:
  - stronger z-direction blur
  - slab-style slice averaging
  - stronger xy partial-volume softening
  - coarse reconstruction-like loss of detail for thick slices
  - additional detail suppression so thick-slice previews look visibly less sharp in both 2D and 3D
- the intended visual outcome is that:
  - `10 mm` is clearly softer than `5 mm`
  - `15 mm` is visibly more coarse
  - `20 mm` looks like a much thicker, lower-detail reconstruction rather than only a slightly blurred one
- the slice-thickness metadata now exposes additional algorithm diagnostics such as:
  - `slab_span_slices`
  - `slab_blend_alpha`
  - `z_reconstruction_scale`
  - `xy_reconstruction_scale`
  - `detail_suppression_alpha`

Relevant files:

- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/types/simulation.ts`

#### 9. Low-kVp / low-mAs noise and artifact simulation was strengthened

- low `kVp` and low `mAs` were judged visually too clean in the earlier implementation
- the noise path was strengthened so low-dose previews now show more obvious image degradation
- the current low-dose model now layers:
  - projection-inspired Poisson/log-count noise
  - stronger material-dependent detector noise
  - correlated noise
  - low-frequency nonuniform noise
  - bone-adjacent streak-like structure
  - photon-starvation-style streak artifacts
  - view-banding / low-dose nonuniformity effects
- the effect strength now depends more explicitly on a low-dose severity term derived from:
  - effective `mAs`
  - selected `kVp`
- the intended practical outcome is that combinations such as:
  - `80 kVp + 30 mAs`
  - `80 kVp + 50 mAs`
  look substantially noisier and more artifact-prone than standard-dose configurations

Relevant files:

- `backend/app/simulation/ct_params_simulator.py`

#### 10. CT parameter preview runtime failure was introduced and fixed in the same pass

During the low-dose artifact enhancement pass, the preview page began failing with:

- `CT parameter preview failed: sequence argument must have length equal to input rank`

Root cause:

- a low-dose artifact helper branch accidentally expanded a `radial` array by one extra dimension
- that silently broadcast one intermediate result from 3-D to 4-D
- a later `gaussian_filter(...)` call still used a 3-element sigma tuple and failed at runtime

Fix:

- the extra singleton expansion on `radial` was removed
- `simulate_ct_scan_params(...)` was re-run locally after the fix and returned valid output again

Why this matters for the next conversation:

- if CT parameter preview starts failing again with a rank/sigma-length style error, inspect the low-dose artifact broadcasting path first

Relevant files:

- `backend/app/simulation/ct_params_simulator.py`

Validation already run for this later pass:

- `npx tsc --noEmit`
- `python -m py_compile backend/app/simulation/ct_params_simulator.py`
- `python -m py_compile backend/app/api/v1/simulation.py backend/app/schemas/simulation.py`
- direct local invocation of `simulate_ct_scan_params(...)` after the rank/broadcast fix

Latest local commit:

- `3d3a1ce` - `Unify LUNG1 workspace flow and refine CT visualization`

Important state:

- this commit exists locally only
- it has been committed but not pushed
- the user plans to continue in a new conversation from this state

### What Was Changed

#### 1. Lung-first data unification

- atlas/workspace defaults were switched from legacy `s0001` to `LUNG1-001`
- available atlas cases now prioritize:
  - `LUNG1-001`
  - `LUNG1-002`
  - `LUNG1-003`
  - legacy `s0001`
- `LUNG1-*` atlas loading now reads local sample DICOM data from:
  - `data/lung_lobe_samples`

Relevant files:

- `backend/app/simulation/phantom_generator.py`
- `backend/app/api/v1/simulation.py`
- `backend/app/schemas/simulation.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/services/simulationService.ts`
- `frontend/src/types/simulation.ts`

#### 2. nnUNet lung-lobe label remapping

- the `nnunet_lung_lobe` output is now remapped into the shared upper-body label ID space
- raw lung-lobe labels `1..5` were mapped into atlas-aligned IDs:
  - `1 -> 13` left lung upper lobe
  - `2 -> 10` left lung lower lobe
  - `3 -> 14` right lung upper lobe
  - `4 -> 12` right lung middle lobe
  - `5 -> 11` right lung lower lobe

Relevant files:

- `backend/app/ai/nnunet_lung_lobe/labels.py`
- `backend/app/ai/nnunet_lung_lobe/__init__.py`
- `backend/app/segmentation/ai/pipeline.py`

#### 3. Pick/world-position alignment chain

- the simulation page now stores both voxel pick position and centered world-mm position
- when the active volume changes, the same picked world point is reprojected back into the current volume
- this was added so the chain:
  - load CT
  - pick position
  - run CT parameter simulation
  - show same physical location
  works more reliably

Relevant files:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/services/simulationService.ts`
- `backend/app/simulation/phantom_generator.py`
- `backend/app/api/v1/simulation.py`

#### 4. Current 3D visualization direction

The user clarified the intended visualization goal:

- bottom layer must be the original CT rendering
- segmentation should only be a color overlay
- `LUNG1` original CT is not "only lung lobes"
- current useful path is:
  - `LUNG1 original CT`
  - plus `nnUNet` lung-lobe segmentation color overlay

What the code currently does:

- 3D view defaults to full-volume display
- `Slice Sync` remains optional and disabled by default
- organ/lung overlay colors were lightened
- default segmentation overlay opacity was reduced to `0.045`
- 3D default no longer forces the prior scan-style view by default

Relevant files:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

#### 5. CT browse / accumulation direction follow-up

After the earlier `LUNG1` visualization pass, the user reported that the browsing / accumulation direction still felt wrong for CT usage.

What was changed in the follow-up fix:

- the axial slice browser no longer always starts from raw `slice 0`
- the page now auto-detects the first and last "informative" body slices using the CT body threshold
- initial slice position now jumps to the topmost meaningful body slice instead of potentially showing empty or misleading superior padding
- slice playback now runs only across the informative body range rather than across the whole raw z extent
- the right-side 3D accumulated CT view now enables slice-sync by default
- the right-side 3D view now defaults to scan-view mode so the visible accumulation direction reads as `head/neck -> ribcage`

Practical outcome:

- left-side browsing now starts closer to the neck / upper chest instead of feeling like it begins from the middle of the body
- right-side 3D volume now visibly accumulates by default instead of staying in full-volume mode
- the intended visual direction is now:
  - neck / upper chest first
  - then downward toward ribcage / lower chest

Relevant files:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

### What The User Explicitly Rejected

These directions were tried and should not be treated as final desired behavior:

- making the 3D default into a mandatory "CT loading / accumulation" mode
- replacing the CT-like appearance with a shell-like organ presentation
- making the overlay too dark or too opaque
- showing a view that feels like only lower-rib / lower-chest content is visible

The user said the "CT loading" default change was bad and wanted the prior accepted version restored.

### Current Known Issues / Open Questions

#### 1. LUNG1 DICOM z ordering may still need deeper validation

The frontend browse / accumulation behavior was adjusted so the page now behaves more like a CT scan from neck to ribcage, but the deeper source-of-truth question is not fully closed yet.

What is improved:

- frontend start slice selection is now body-aware
- 3D accumulation is now on by default
- 3D scan-view orientation was adjusted to better match the requested visual direction

What may still need future validation:

- whether the source DICOM series ordering itself is always anatomically correct for all real studies
- whether `_normalize_dicom_scan_direction(...)` and the `LUNG1-*` lung-sample loader make the correct flip decision for every dataset
- whether some remaining cases are really data-order issues rather than only camera / clipping perception issues

#### 2. LUNG1 atlas path still does not provide a full organ label map

- current `LUNG1-*` metadata uses `label_map: {}`
- therefore the true useful segmentation overlay for `LUNG1` should come from model output, especially `nnunet_lung_lobe`
- if future work wants visible lung-lobe overlay in the workspace automatically, the next step is likely to connect workspace loading with segmentation inference or cached segmentation results

#### 3. Visualization target is now narrower and clearer

The correct target for the next conversation is not "full-body multi-organ atlas style rendering".

The correct target is:

- keep `LUNG1` original CT visible as the main 3D body
- overlay segmentation colors lightly
- for now the segmentation of interest is mainly lung-lobe segmentation from `nnUNet`

### Validation Already Run

- `npx tsc --noEmit` passed multiple times during the frontend visualization adjustments
- backend Python compile checks had already passed during the earlier `LUNG1` integration pass
- `npx tsc --noEmit` also passed after the follow-up slice-range / 3D scan-direction fixes

### Current Files Most Relevant For The Next Conversation

- `CODEX_PROJECT_CONTEXT.md`
- `backend/app/simulation/phantom_generator.py`
- `backend/app/api/v1/simulation.py`
- `backend/app/ai/nnunet_lung_lobe/labels.py`
- `backend/app/segmentation/ai/pipeline.py`
- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/services/simulationService.ts`
- `frontend/src/types/simulation.ts`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

## Current Status

This context file reflects the latest CT simulation, layout, Docker, and performance work completed in `D:\0proj\MedSim_Studio-dev` as of 2026-07-09.

Repository state at the time of this update:

- local `main` and `origin/main` were synchronized when this context sequence began
- the earlier context referenced pushed commit `c44d578` (`checkpoint ct params simulation state`)
- the current workspace now contains additional uncommitted CT simulation changes beyond that checkpoint

The current simulation page is a simplified single-workspace CT page:

- left: one main axial CT slice
- right: one synchronized 3D accumulated body view
- below: collapsible CT parameter controls

Current browse / accumulation behavior:

- left-side slice browsing starts at the first informative upper-body slice rather than blindly at raw z index `0`
- autoplay advances through the informative body slice range only
- right-side 3D CT accumulation is enabled by default
- right-side 3D scan view is oriented to read as `head/neck -> ribcage`

The current CT parameter simulation still supports manual execution only:

- changing parameters does not auto-run
- user must click `Run CT Parameter Simulation`

The current angle model supports:

- `gantry_pitch_deg`
- `gantry_yaw_deg`
- `gantry_roll_deg`

The legacy frontend `gantryTiltDeg` field has now been removed from the active CT params UI/request path. Backend compatibility handling for legacy `gantry_tilt_deg` still exists in some normalization / metadata paths, but the intended model going forward is fully 3-axis gantry pose.

## User Requirements Captured

- Remove redundant CT simulation visuals that made the page crowded.
- Delete the extra middle 3D volume and the lower-left original CT slice from the old layout.
- Re-layout the page to be clean, useful, and visually simpler.
- Refactor Docker so rebuild/restart waiting time is shorter.
- Fix the real reason simulation loading was slow; do not only increase request timeout.
- Improve CT parameter realism so the effect of parameter changes is more convincing.
- Unify interaction behavior so angle changes do not auto-trigger simulation while other parameters require a button click.
- Expand angle control beyond only pitch-like gantry tilt to more realistic multi-axis controls.
- Update this context file before starting a new conversation.

## Documents Read

- `docs/interface_ct_simulation_to_artifact.md`
- `CODEX_PROJECT_CONTEXT.md`

## Code Areas Read / Modified

Backend:

- `backend/app/api/v1/simulation.py`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `backend/app/simulation/phantom_generator.py`
- `backend/app/simulation/volume_builder.py`

Frontend:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/services/api.ts`
- `frontend/src/services/simulationService.ts`
- `frontend/src/types/simulation.ts`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

Infra / Docker:

- `docker-compose.yml`
- `docker/backend/Dockerfile`
- `docker/frontend/Dockerfile`
- `.dockerignore`
- `backend/.dockerignore`
- `backend/requirements.runtime.txt`

## Current Simulation Page Behavior

### Layout

- The page no longer shows the old redundant comparison layout.
- The page now uses one active CT data source for display:
  - if `ctParamsResult` exists, both 2D and 3D render the simulated CT
  - otherwise both render the loaded phantom
- The main slice and the 3D accumulation stay synchronized through the same current slice index.
- The CT parameter panel is collapsible and placed below the image workspace.
- The page is vertically scrollable and no longer clipped by the old fixed-height structure.

### Interaction

- The CT workspace now supports three input sources:
  - `atlas`
  - `procedural`
  - `dicom`
- The toolbar can load any of those three sources into the same slice/3D workspace.
- When the active workspace source is `dicom`, the page exposes study/series selection before loading the CT workspace volume.
- `Run CT Parameter Simulation` is the only trigger for simulation execution.
- Changing `pitch / yaw / roll / thickness / dose / mAs / kVp / pitch / FOV / matrix / kernel / contrast phase` does not auto-run.
- CT angle controls now include a `Reset Angles` action that sets `pitch / yaw / roll` back to `0°` in one click.
- A new simulation result now preserves the current slice index as far as possible instead of always resetting to slice `0`.
- Backend center-slice preview stats are now surfaced in the result UI.

### Current Angle Controls

Frontend UI now exposes:

- `Pitch (deg)`:
  up/down tilt around the patient left-right axis
- `Yaw (deg)`:
  left/right turning around the anterior-posterior axis
- `Roll (deg)`:
  side tilt around the head-to-feet axis
- `Reset Angles`:
  restores all three angle controls to the original `0°` pose

Range is currently `-30` to `30` degrees for each axis.

## Backend CT Parameter Simulation

### High-Level Pipeline

`backend/app/simulation/ct_params_simulator.py` currently performs these image-domain steps:

1. gantry pose resampling
2. slice thickness effect
3. dose / mAs noise
4. kVp remap
5. pitch degradation
6. FOV adjustment
7. matrix resolution effect
8. reconstruction kernel effect
9. contrast phase enhancement

### Gantry Pose

The simulator no longer only applies one `gantry_tilt_deg`.

It now constructs a 3D pose from:

- `gantry_pitch_deg`
- `gantry_yaw_deg`
- `gantry_roll_deg`

Implementation notes:

- array convention is `(z, y, x)`
- resampling is spacing-aware
- CT interpolation uses linear interpolation
- label interpolation uses nearest-neighbor interpolation
- background fill uses air HU (`-1000`)
- output shape may expand in more than one dimension depending on combined rotation
- algorithm step name is now `gantry_pose_resampling`

Backward compatibility:

- `_resolve_params(...)` still accepts legacy `gantry_tilt_deg`
- legacy tilt is mapped to `gantry_pitch_deg`
- metadata still includes `gantry_tilt_deg` as a compatibility mirror of pitch in some backend responses

### Realism Improvements Already Implemented

#### Dose / mAs

- noise is no longer simple uniform Gaussian only
- now uses projection-inspired Poisson/log-count noise plus detector/electronic noise
- noise remains material-dependent and spatially varying
- current noise scaling depends on:
  - `dose_level`
  - `mAs`
  - `pitch`
  - `slice_thickness_mm`
  - `kVp`
- metadata now includes:
  - `effective_mAs`
  - `photon_flux_reference`
  - `bowtie_fluence_range`

#### kVp

- no longer just global contrast scaling
- now uses piecewise material-specific HU remapping plus empirical beam-hardening response
- vascular / iodine-like regions are boosted more strongly at low `kVp`
- dense bone response is also now kVp-sensitive in a more nonlinear way

#### Slice Thickness

- now uses z-blur plus slight xy partial-volume blur
- thicker slices reduce z detail and slightly soften in-plane boundaries

#### Pitch

- now uses helical interpolation-style blending between adjacent slices
- applies z-direction blur at higher pitch
- adds windmill/interpolation-like artifacts for high pitch
- metadata now includes `helical_blend_alpha`

#### FOV

- no longer only rescales and crops/pads
- small FOV now also introduces empirical cupping and truncation-edge behavior
- metadata now includes:
  - `cupping_strength_hu`
  - `truncation_edge_boost_hu`

#### Matrix Size

- low matrix settings now use downsample/restore behavior with anti-aliasing
- `256` shows expected loss of in-plane detail
- `1024` no longer behaves identically to `512`
- current `1024` handling uses mild edge/detail enhancement, but it remains conservative and is still one of the realism gaps

#### Reconstruction Kernel

- `smooth / soft` produce stronger smoothing
- `standard` remains balanced
- `lung / bone / sharp` produce sharper appearance with stronger high-frequency retention
- kernel behavior now also includes more CT-like texture / artifact personality:
  - `smooth / soft`: light ring artifact
  - `standard`: faint ring artifact
  - `lung`: extra high-frequency granular texture
  - `bone / sharp`: stronger edge ringing

#### Contrast Phase

- enhancement is organ-weighted when labels are available
- liver, kidneys, spleen, pancreas and coarse airway proxy receive phase-specific boosts
- fallback path still applies empirical HU boosts without labels
- enhancement is smoother and less voxel-wise than before
- now also includes empirical wash-in / washout behavior across `arterial / venous / delayed`
- metadata now includes:
  - `vascular_emphasis`
  - `washout_strength`

## DICOM Workspace / Spatial Metadata

### Workspace Source Loading

`backend/app/api/v1/simulation.py` now supports CT workspace loading for:

- `atlas`
- `procedural`
- `dicom`

For `dicom` source:

- `/api/v1/simulation/phantom` now accepts `study_id` / `series_id`
- the selected CT series is reconstructed into a zyx volume
- the workspace response returns:
  - `origin`
  - `direction`
  - `spatial_reference`
  - `study_id`
  - `series_id`

### Volume Builder

`backend/app/simulation/volume_builder.py` was strengthened:

- DICOM slices now use `ImagePositionPatient` / `ImageOrientationPatient` when available
- sorting, `origin`, and `direction` are no longer limited to the earlier z-only / identity fallback model
- metadata now includes:
  - `spatial_reference = dicom_patient_space`

This is a major improvement over the earlier DICOM path, but broader runtime validation is still needed for difficult orientation / metadata edge cases.

## Frontend CT Types / Request Flow

### Request/Response Naming

- `frontend/src/services/api.ts` converts camelCase request payloads to snake_case
- backend responses are converted back to camelCase automatically

### Current Frontend Types

`frontend/src/types/simulation.ts` now includes:

- `gantryPitchDeg`
- `gantryYawDeg`
- `gantryRollDeg`
- CT workspace / preview `source: 'atlas' | 'procedural' | 'dicom'`
- `studyId?`
- `seriesId?`
- `spatialReference?`

### Current Run Path

- `SimulationPage.tsx` builds `CtParamsPreviewParams`
- `simulationService.runCtParamsPreview(...)` sends the request
- backend schema now expects 3-axis fields
- frontend no longer uses `gantryTiltDeg` in the active CT params UI path
- CT workspace loading also uses `/api/v1/simulation/phantom` for:
  - `atlas`
  - `procedural`
  - `dicom`

## Performance / Loading Investigation

### Root Cause Found

The earlier simulation/phantom loading slowness was not mainly backend compute time.

The main issue was that the frontend had been built with:

- `VITE_API_BASE_URL=http://localhost:8000/api/v1`

which bypassed the frontend proxy path.

It was corrected to:

- `VITE_API_BASE_URL=/api/v1`

so browser requests now go through the intended frontend proxy path.

### Additional Improvements

- `frontend/src/services/simulationService.ts`
  - CT workspace and CT preview requests now use a longer timeout as a fallback safeguard
- `backend/app/api/v1/simulation.py`
  - atlas/procedural workspace payloads are cached with `lru_cache`
  - repeated atlas/procedural requests become substantially faster

### Practical Outcome

- CT workspace loading became much faster after proxy correction
- repeated atlas/procedural loads are faster because of backend payload caching
- the previous browser failure was not just a timeout problem

## Docker / Rebuild Refactor

### Goals

- reduce rebuild waiting time
- simplify compose structure
- fix frontend/backend wiring

### Main Changes

#### `docker-compose.yml`

- reworked with shared anchors for cleaner service definitions
- frontend build context is now the `frontend` directory
- frontend build arg forces `VITE_API_BASE_URL=/api/v1`
- hardcoded container naming was removed from compose definitions

#### `docker/backend/Dockerfile`

- supports build args such as:
  - `APT_MIRROR`
  - `PIP_INDEX_URL`
  - `PIP_TRUSTED_HOST`
- switched runtime install path to `backend/requirements.runtime.txt`

#### `docker/frontend/Dockerfile`

- supports `NPM_REGISTRY`
- uses npm cache mount
- takes `VITE_API_BASE_URL` as build arg/env
- corrected copy behavior for the frontend-only build context

#### Ignore Files

- root `.dockerignore` was tightened significantly
- `backend/.dockerignore` was added
- large unrelated directories are excluded from backend build context to improve build speed

### Container Conflict Issue Encountered

At one point the user hit a MinIO container name conflict during manual rebuild.

Key takeaway:

- current compose stack should be started from the project root
- if conflicts appear, a clean `docker compose down` followed by `docker compose up --build -d` resolves it
- later checks confirmed the stack was healthy again

## Acceptance / Validation Already Performed

### Static Checks

Passed:

- `npx tsc --noEmit`
- `python -m py_compile backend/app/simulation/ct_params_simulator.py`
- `python -m py_compile backend/app/api/v1/simulation.py`
- `python -m py_compile backend/app/simulation/volume_builder.py`
- `python -m py_compile backend/app/schemas/simulation.py`
- `docker compose config`

### Runtime / Service Checks

Confirmed healthy at the time of validation:

- `backend`
- `frontend`
- `postgres`
- `minio`

Frontend URL used during validation:

- `http://localhost:5173`

### CT Parameter Trend Validation

Automated checks were run against `/api/v1/simulation/ct-params/preview`.

Observed trends were consistent with expectation:

- low dose / low `mAs`:
  noisier
- high `mAs`:
  smoother / more stable
- thick slice:
  blurrier, lower z-resolution
- thin slice:
  sharper, noisier
- `sharp / bone` kernel:
  stronger edge emphasis
- `smooth` kernel:
  more blurred
- `256` matrix:
  lower in-plane detail
- gantry angle changes:
  true output geometry/resampling change, not just display rotation
- lower dose / lower effective mAs:
  stronger projection-like noise and streak tendency
- higher pitch:
  more helical interpolation blur / windmill-like artifact tendency
- different kernels:
  differ not only in blur/sharpness, but also in texture/ringing character

### Acceptance Conclusion at That Time

The implementation was considered acceptable for continued integration and demo use:

- functional completeness: good
- realism: improved but still approximate
- layout: substantially improved
- Docker usability: improved

## Additional Review Notes From Latest Pass

- backend parameter simulation quality is currently better than the frontend result presentation makes it look
- the current page is demo-usable, but the effect communication is still weaker than the underlying implementation
- the latest review found that:
  - the former `1024 ~= 512` issue has been partially addressed, but `1024` is still conservative
  - the page no longer forcibly resets to slice `0` after each CT parameter run
  - backend center-slice preview stats are now surfaced in the result UI
  - the CT workspace no longer hardcodes atlas-only wording or atlas-only loading assumptions
  - DICOM CT workspace loading now exists, but end-to-end browser validation for all DICOM edge cases is still limited

## Known Remaining Gaps

- the simulator is still not a full sinogram / reconstruction physics model; it remains an advanced image-domain / projection-inspired approximation
- `1024` matrix effect is still conservative and not as strong as a highly realistic high-resolution acquisition model
- contrast-phase differentiation is improved but still empirical rather than perfusion-model-based
- reconstruction kernel behavior is more realistic than before, but still not MTF / NPS calibrated
- slice-thickness modeling is still blur-based and not derived from true SSP / reconstruction geometry
- DICOM CT workspace loading exists, but broader runtime validation is still needed for difficult orientation / metadata edge cases
- frontend result presentation is improved, but still does not fully explain all backend metadata / algorithm-step details
- this context file is now updated, but other docs such as `docs/interface_ct_simulation_to_artifact.md` were not fully synchronized in this pass

## Suggested Next TODOs

Priority items for the next conversation:

1. Add backend regression tests for CT parameter trend behavior
   - dose / mAs noise monotonicity
   - pitch artifact strength trend
   - kernel texture / ringing differences
   - contrast phase wash-in / washout ordering
2. Improve kernel realism toward MTF / NPS-driven behavior
3. Improve slice-thickness realism toward SSP-like behavior instead of blur-only approximation
4. Do runtime browser validation for DICOM workspace loading across several real CT series with different orientations / metadata completeness
5. Improve frontend result presentation so more of `params_json.algorithm_steps` and key derived metadata are visible without opening raw JSON
6. Synchronize `docs/interface_ct_simulation_to_artifact.md` with the current implementation

## Most Important Files For Next Conversation

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/types/simulation.ts`
- `frontend/src/services/simulationService.ts`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `backend/app/simulation/volume_builder.py`
- `backend/app/api/v1/simulation.py`
- `docker-compose.yml`
- `docker/frontend/Dockerfile`
- `docker/backend/Dockerfile`

## Recommended Starting Point For Next Agent

If continuing CT simulation work, inspect these items first:

1. `SimulationPage.tsx`
   - CT workspace source switching (`atlas / procedural / dicom`)
   - current slice/3D synchronization
   - current result panel / center-slice stats display
   - current `Reset Angles` control
2. `ct_params_simulator.py`
   - current 3-axis gantry pose implementation
   - current projection-inspired noise model
   - current helical / FOV / kernel / contrast-phase approximations
3. `volume_builder.py`
   - DICOM orientation / origin / direction reconstruction
4. `simulation.py`
   - workspace source loading behavior
   - atlas / procedural caching
   - CT preview endpoint behavior
   - DICOM workspace load path
5. `docker-compose.yml` and Dockerfiles
   - current build wiring
   - frontend API proxy build arg
