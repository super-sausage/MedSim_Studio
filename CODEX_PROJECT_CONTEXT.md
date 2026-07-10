# MedSim Studio Codex Project Context

## Update 2026-07-10

This section captures the latest `LUNG1` integration and CT workspace / visualization changes completed on 2026-07-10, including the follow-up scan-order fixes made later the same day.

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
