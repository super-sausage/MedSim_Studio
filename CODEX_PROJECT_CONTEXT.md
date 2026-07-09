# MedSim Studio Codex Project Context

## Current Status

This context file reflects the latest CT simulation, layout, Docker, and performance work completed in `D:\0proj\MedSim_Studio-dev` as of 2026-07-09.

The current simulation page is a simplified single-workspace CT page:

- left: one main axial CT slice
- right: one synchronized 3D accumulated body view
- below: collapsible CT parameter controls

The current CT parameter simulation supports manual execution only:

- changing parameters does not auto-run
- user must click `Run CT Parameter Simulation`

The current angle model is no longer single-axis only. It now supports:

- `gantry_pitch_deg`
- `gantry_yaw_deg`
- `gantry_roll_deg`

There is still temporary backward-compatibility handling for legacy `gantry_tilt_deg` references in some frontend/backend paths, but the intended model going forward is 3-axis gantry pose.

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

- `Generate Phantom` loads the atlas phantom for CT parameter exploration.
- `Run CT Parameter Simulation` is the only trigger for simulation execution.
- Changing `pitch / yaw / roll / thickness / dose / mAs / kVp / pitch / FOV / matrix / kernel / contrast phase` does not auto-run.
- A new simulation result resets the slice index to the first slice and stops playback.

### Current Angle Controls

Frontend UI now exposes:

- `Pitch (deg)`:
  up/down tilt around the patient left-right axis
- `Yaw (deg)`:
  left/right turning around the anterior-posterior axis
- `Roll (deg)`:
  side tilt around the head-to-feet axis

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
- now uses heteroscedastic, material-dependent, partly correlated noise
- noise scaling depends on:
  - `dose_level`
  - `mAs`
  - `pitch`
  - `slice_thickness_mm`

#### kVp

- no longer just global contrast scaling
- now uses piecewise material-specific HU remapping
- vascular / iodine-like regions are boosted more strongly at low `kVp`

#### Slice Thickness

- now uses z-blur plus slight xy partial-volume blur
- thicker slices reduce z detail and slightly soften in-plane boundaries

#### Pitch

- applies z-direction blur at higher pitch
- adds mild interpolation-ripple-like artifact for high pitch

#### Matrix Size

- low matrix settings now use downsample/restore behavior with anti-aliasing
- `256` shows expected loss of in-plane detail
- `1024` is still relatively conservative and is one of the remaining realism gaps

#### Reconstruction Kernel

- `smooth / soft` produce stronger smoothing
- `standard` remains balanced
- `lung / bone / sharp` produce sharper appearance with stronger high-frequency retention

#### Contrast Phase

- enhancement is organ-weighted when labels are available
- liver, kidneys, spleen, pancreas and coarse airway proxy receive phase-specific boosts
- fallback path still applies empirical HU boosts without labels
- enhancement is smoother and less voxel-wise than before

## Frontend CT Types / Request Flow

### Request/Response Naming

- `frontend/src/services/api.ts` converts camelCase request payloads to snake_case
- backend responses are converted back to camelCase automatically

### Current Frontend Types

`frontend/src/types/simulation.ts` now includes:

- `gantryPitchDeg`
- `gantryYawDeg`
- `gantryRollDeg`

and keeps optional legacy:

- `gantryTiltDeg?`

for compatibility during transition.

### Current Run Path

- `SimulationPage.tsx` builds `CtParamsPreviewParams`
- `simulationService.runCtParamsPreview(...)` sends the request
- backend schema now expects 3-axis fields
- frontend fallback logic still maps legacy `gantryTiltDeg` into `gantryPitchDeg` if needed

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
  - CT phantom and CT preview requests now use a longer timeout as a fallback safeguard
- `backend/app/api/v1/simulation.py`
  - atlas/procedural phantom generation payloads are cached with `lru_cache`
  - repeated phantom requests become substantially faster

### Practical Outcome

- phantom loading became much faster after proxy correction
- repeated phantom loads are faster because of backend payload caching
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

### Acceptance Conclusion at That Time

The implementation was considered acceptable for continued integration and demo use:

- functional completeness: good
- realism: improved but still approximate
- layout: substantially improved
- Docker usability: improved

## Known Remaining Gaps

- `1024` matrix effect is still conservative and not as strong as a highly realistic high-resolution acquisition model
- contrast-phase differentiation is improved but still empirical
- dose/noise remains image-domain approximation, not physics-based projection/reconstruction
- some frontend compatibility residue still references `gantryTiltDeg`; intended future cleanup is to fully remove the legacy field after transition
- this context file is now updated, but other docs such as `docs/interface_ct_simulation_to_artifact.md` were not fully synchronized in this pass

## Most Important Files For Next Conversation

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/types/simulation.ts`
- `frontend/src/services/simulationService.ts`
- `backend/app/schemas/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `backend/app/api/v1/simulation.py`
- `docker-compose.yml`
- `docker/frontend/Dockerfile`
- `docker/backend/Dockerfile`

## Recommended Starting Point For Next Agent

If continuing CT simulation work, inspect these items first:

1. `SimulationPage.tsx`
   - current parameter controls
   - current slice/3D synchronization
   - remaining legacy `gantryTiltDeg` fallback
2. `ct_params_simulator.py`
   - current 3-axis gantry pose implementation
   - current realism approximations
3. `simulation.py`
   - phantom caching
   - CT preview endpoint behavior
4. `docker-compose.yml` and Dockerfiles
   - current build wiring
   - frontend API proxy build arg
