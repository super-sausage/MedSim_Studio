# MedSim Studio Codex Project Context

## Current Task

Implement the first new CT simulation feature in `D:\0proj\MedSim_Studio-dev`:

- Add gantry tilt control to the CT simulation UI.
- Use real 3D rotation plus resampling to change the actual axial slice plane.
- Do not use frontend-only image rotation.

## User Requirements Captured

- UI adds `gantry_tilt_deg` in degrees.
- Suggested range: `-30` to `30`.
- Default: `0`.
- Rotation should follow common CT gantry tilt semantics:
  around the patient left-right axis.
- CT intensity interpolation must be linear.
- Organ labels must rotate with nearest-neighbor interpolation.
- Background fill should use air HU, e.g. `-1000`.
- Avoid obvious body cropping after tilt.
- Keep `head_to_feet`, slice playback, window/level, organ overlay, and 3D display working.
- Frontend changing the angle should trigger regeneration/re-request of the corresponding simulated volume and reset the current slice to the first slice.
- Backend API params, TypeScript types, and response metadata must all include `gantry_tilt_deg`.
- Do not implement kVp/mAs/slice thickness changes beyond existing behavior; this task only adds gantry tilt.

## Documents Read

- `docs/interface_ct_simulation_to_artifact.md`

Note:

- The repository root did not contain `CODEX_PROJECT_CONTEXT.md` at the time this task started, so this file is being created now as the canonical task context record requested by the user.

## Code Files Already Read

Backend:

- `backend/app/simulation/phantom_generator.py`
- `backend/app/api/v1/simulation.py`
- `backend/app/simulation/ct_params_simulator.py`
- `backend/app/schemas/simulation.py`

Frontend:

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/services/simulationService.ts`
- `frontend/src/services/api.ts`
- `frontend/src/types/simulation.ts`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

## Current Understanding

### Backend

- `backend/app/simulation/ct_params_simulator.py` currently performs:
  - slice-thickness approximation,
  - dose noise,
  - kVp contrast remap,
  - pitch degradation,
  - FOV resampling,
  - matrix resampling,
  - reconstruction kernel effect,
  - empirical contrast enhancement.
- It does not currently change the acquisition plane orientation.
- `simulate_ct_scan_params()` already accepts an optional `label_volume`, but labels are only used for contrast-phase enhancement and are not geometrically transformed.
- `backend/app/api/v1/simulation.py` already threads CT preview requests from atlas/procedural/DICOM sources into `simulate_ct_scan_params()`.
- `backend/app/schemas/simulation.py` defines the request schema for CT parameter preview and is the correct place to add `gantry_tilt_deg`.

### Frontend

- `frontend/src/services/api.ts` automatically converts request body keys from camelCase to snake_case and backend responses back to camelCase.
- `frontend/src/types/simulation.ts` defines `CtParamsPreviewParams`, request/response metadata types, and standardized-case types.
- `frontend/src/pages/SimulationPage.tsx` already has the CT parameter simulation panel and a manual "Run CT Parameter Simulation" action.
- The original phantom slice and 3D volume preview come from the phantom endpoint.
- The simulated CT result is shown in the comparison panel from `ctParamsResult.simulatedVolumeBase64`.
- The current simulated preview flow does not include any gantry tilt parameter.

## Planned Implementation

### Backend changes

1. Add `gantry_tilt_deg` to `CTParamsPreviewParams` in `backend/app/schemas/simulation.py`.
2. Extend parameter resolution in `backend/app/simulation/ct_params_simulator.py`.
3. Add a gantry tilt transform step before downstream image-domain effects:
   - rotate around the patient left-right axis,
   - work in physical spacing-aware coordinates,
   - preserve output shape,
   - use padding to avoid obvious truncation,
   - use linear interpolation for CT,
   - use nearest-neighbor interpolation for labels.
4. Include the angle in:
   - `metadata`,
   - `params_json`,
   - algorithm step records,
   - standardized output payload built by the API.

### Frontend changes

1. Add `gantryTiltDeg` to `CtParamsPreviewParams` in `frontend/src/types/simulation.ts`.
2. Add UI control in `frontend/src/pages/SimulationPage.tsx`:
   - numeric/range input,
   - display in degrees,
   - default `0`,
   - constrained to `-30..30`.
3. Send the value through `simulationService.runCtParamsPreview(...)`.
4. Reset slice index to the first slice when a new simulated volume is generated for a changed angle.
5. Show returned angle metadata in the result panel if useful.

## Validation Plan

After implementation:

- Run Python compile checks for the modified backend files.
- Run frontend TypeScript type-check.
- Verify the API request/response field naming is correct through the existing camel/snake conversion layer.
- Confirm the simulated output metadata includes `gantry_tilt_deg`.

## Implemented Changes

### Backend

- Updated `backend/app/schemas/simulation.py`
  - Added `gantry_tilt_deg` to `CTParamsPreviewParams`.
- Updated `backend/app/simulation/ct_params_simulator.py`
  - Added a new `gantry_tilt_resampling` step before the existing image-domain parameter effects.
  - Implemented spacing-aware affine resampling with `scipy.ndimage.affine_transform`.
  - Rotation axis follows patient left-right direction, represented as the array `x` axis in the project convention `(z, y, x)`.
  - CT volume uses linear interpolation (`order=1`).
  - Label volume uses nearest-neighbor interpolation (`order=0`).
  - Background fill uses `-1000 HU`.
  - Output keeps the original slice count to preserve scan playback semantics, while expanding the anterior-posterior dimension as needed to reduce obvious clipping after tilt.
  - Added `gantry_tilt_deg` to returned metadata.

### Frontend

- Updated `frontend/src/types/simulation.ts`
  - Added `gantryTiltDeg` to `CtParamsPreviewParams`.
  - Added `gantryTiltDeg` to CT preview metadata typing.
- Updated `frontend/src/pages/SimulationPage.tsx`
  - Added gantry tilt UI control:
    - range input,
    - numeric input,
    - default `0`,
    - constrained to `-30..30`.
  - Angle changes trigger automatic CT preview refresh with debounce.
  - A new simulated result resets the slice index to the first slice and stops playback.
  - Simulated slice rendering now uses the simulated volume metadata shape instead of assuming the original phantom dimensions.
  - Added gantry tilt display in the result summary panel.

## Rotation / Resampling Principle

- Array convention is `(z, y, x)`.
- The gantry tilt rotation is applied around the patient left-right axis, which corresponds to the `x` axis.
- The transform is spacing-aware:
  voxel indices are converted conceptually into physical coordinates using `(z_spacing, y_spacing, x_spacing)`, rotated, then sampled back through the inverse transform.
- `affine_transform()` is used so the resulting axial stack represents a genuinely changed slice plane, not a 2D post-render image rotation.

## API / Metadata Notes

- New request parameter:
  - `params.gantry_tilt_deg`
- New response metadata field:
  - `metadata.gantry_tilt_deg`
- `params_json.requested_params` and `params_json.resolved_params` also include the angle.

## Validation Performed

- Python compile check passed:
  - `backend/app/simulation/ct_params_simulator.py`
  - `backend/app/api/v1/simulation.py`
  - `backend/app/schemas/simulation.py`
- Frontend TypeScript type-check passed:
  - `frontend` with `npx tsc --noEmit`

## Remaining Notes

- No end-to-end browser/manual runtime verification has been executed yet in this session.
- `docs/interface_ct_simulation_to_artifact.md` was read for context but not yet updated; the user request only explicitly required updating this root context file.

## 2026-07-05 CT Layout Update

### Task Scope

- Keep the CT parameter simulation data flow and gantry tilt feature.
- Change the CT simulation page layout to:
  - left: one 2D slice view only,
  - right: one 3D accumulated body view only.
- Remove meaningless original/simulated comparison panels and metadata-heavy result presentation.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

### Frontend Layout Changes

- Removed the extra `Original CT Slice` and `Simulated CT Slice` comparison area.
- Removed the `Simulation Result` panel.
- Removed scan-direction prompt text and visible shape/dimension text from the CT simulation page.
- Kept existing controls that are still functionally relevant:
  - generate phantom,
  - play/pause,
  - slice slider,
  - speed,
  - window/level presets,
  - pick position,
  - organ label overlay,
  - CT parameter controls including gantry tilt.

### Shared Data Principle

- The page now computes one active CT volume for display:
  - if CT parameter simulation result exists, both left 2D and right 3D use `ctParamsResult.simulatedVolumeBase64`,
  - otherwise they fall back to the loaded phantom.
- The right 3D volume uses the same decoded simulated stack as the left 2D slice.
- `syntheticClipIndex={sliceIndex}` is still passed into `VolumeRenderer`, so when the current slice is `N`, the 3D body is clipped to show only slices `1..N`.
- When gantry tilt changes and a new simulated result returns, the current slice resets to the first slice and playback stops, so both views restart from the same simulated stack.

### 2D / 3D Scaling Fix

- The 2D canvas is now wrapped in an aspect-ratio container derived from the active volume shape `(width / height)`, so the slice keeps the correct axial proportion instead of stretching to fill the panel.
- The 3D renderer no longer relies on the previous coarse `resetCamera()` result.
- `VolumeRenderer` now:
  - centers synthetic volume origin using voxel-center-aware spacing math,
  - computes world-space bounds from the actual volume,
  - derives camera focal point from the bounds center,
  - computes distance from the volume radius plus container aspect ratio,
  - reapplies the camera fit on resize.
- This addresses the oversized / off-center body issue using real spacing and bounds rather than CSS scaling hacks.

### Validation Performed For This Layout Update

- Frontend TypeScript check passed:
  - `frontend`: `npx tsc --noEmit`
- Frontend production build passed:
  - `frontend`: `npm run build`
- Build initially failed inside sandbox because Vite/esbuild could not spawn a child process (`EPERM`); rerunning outside the sandbox succeeded.

### Residual Notes

- The backend API shape was not changed for this layout task.
- No browser-interaction screenshot verification was performed in this session; validation was done through code inspection plus type/build checks.

## 2026-07-06 CT Simulation Layout Scroll Fix

### Task Scope

- Fix the CT simulation page layout so the page can scroll vertically.
- Preserve CT simulation, gantry tilt, slice playback, and 3D accumulation logic.
- Move the CT parameter panel below the image area and collapse it by default.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Layout Changes

- Changed the page root from fixed `h-full` behavior to `min-h-full` so the simulation page can grow with content instead of being clipped.
- Reworked the CT phantom tab to use a scrollable content column (`overflow-y-auto`) instead of nested `overflow-hidden` containers.
- Moved the 2D slice view and 3D accumulated volume into a top image grid with equal-width panels on large screens.
- Set both primary image panels to `min-h-[520px]` so the slice and 3D body remain the dominant first-screen content.
- Kept the compact playback / slice / window / picking / label controls directly below the image area.
- Converted `CT Scan Params Simulation` into a collapsible panel controlled by an explicit expand/collapse button.
- Left the CT parameter controls and run action intact, but rendered them only when the panel is expanded so they no longer compress the image area by default.

### Validation Plan For This Fix

- Run frontend TypeScript type-check after the JSX/layout changes.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT 3D Accumulated Clipping Restore

### Task Scope

- Keep the current upper-body CT data source unchanged.
- Restore the right-side 3D display to accumulated clipping driven by the current axial slice.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- Restored `syntheticClipIndex={sliceIndex}` on the `VolumeRenderer` usage in the CT simulation page.
- The right-side 3D panel again shows only the accumulated volume from the first slice through the current slice index.
- The left-side 2D slice browsing, slice slider, playback, gantry tilt preview refresh, and CT parameter logic were not changed.

### Validation Plan For This Update

- Run frontend TypeScript type-check after the prop change.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT Top-To-Bottom Display Alignment

### Task Scope

- Keep accumulated clipping behavior.
- Change the frontend CT display order so the visible progression is from upper body to lower body.
- Keep the left 2D slice and right 3D accumulated rendering aligned to the same display order.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- This attempt introduced a frontend-only z-axis reversal for the displayed CT stack before 2D rendering and VTK payload preparation.
- That approach was later identified as incorrect for the current `head_to_feet` data path because the loaded phantom data already uses the intended top-to-bottom z ordering.
- The display stack reversal should not be considered the final intended behavior.

### Validation Plan For This Update

- Run frontend TypeScript type-check after the display-order change.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT 3D High-To-Low Clipping Fix

### Task Scope

- Keep the left 2D display order aligned with the user-facing top-to-bottom slice progression.
- Fix the right 3D accumulated clipping so it grows from upper body toward lower body instead of the reverse.

### Files Modified

- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`
- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- Added explicit `syntheticClipDirection` handling to `VolumeRenderer`.
- A temporary page-level `high_to_low` override was tried for the CT simulation page.
- That page-level override was later removed after confirming the primary issue was the extra frontend z reversal rather than the base progressive clipping direction.

## 2026-07-06 CT Display Order Correction

### Task Scope

- Remove the incorrect frontend z reversal that made the 3D body appear fully present and then clip in the wrong anatomical direction.
- Restore the CT simulation page to use the backend-provided `head_to_feet` volume ordering directly for both 2D and 3D rendering.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- Removed the temporary `reverseVolumeZ(...)` display transform from the CT simulation page.
- The 2D slice viewer and 3D accumulated rendering now both consume the same original decoded volume order again.
- Removed the temporary page-level `syntheticClipDirection="high_to_low"` override from the CT simulation page.

### Validation Plan For This Update

- Run frontend TypeScript type-check after removing the temporary display reversal.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT Scan Direction Final Adjustment

### Task Scope

- Keep the restored original volume ordering.
- Make the right-side 3D accumulated clipping advance in CT-style top-to-bottom order.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- The CT simulation page now explicitly passes `syntheticClipDirection="high_to_low"` to the right-side `VolumeRenderer`.
- This keeps the current body rendering intact while changing only the accumulated clipping progression direction.
- Left-side 2D slice rendering order was not changed in this step.

### Validation Plan For This Update

- Run frontend TypeScript type-check after the page-level clipping-direction adjustment.
- Browser/manual verification was not executed in this session.

### Validation Plan For This Update

- Run frontend TypeScript type-check after the clipping-direction change.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT 3D Progressive Accumulation Direction Fix

### Task Scope

- Fix the right-side CT 3D accumulation so it keeps slices `0..N` instead of showing the full body first and then clipping it away.
- Keep the existing gantry tilt, playback, slider, window/level, organ label overlay, and page layout unchanged.
- Keep the frontend on the same simulated CT volume and same current slice index for both the left 2D slice and right 3D volume.

### Root Cause

- The CT simulation page explicitly passed `syntheticClipDirection="high_to_low"` into `VolumeRenderer`.
- In `VolumeRenderer`, that branch had different clipping-plane semantics from the default path:
  - at `sliceIndex=0`, it added no clipping plane at all, so the first frame rendered the full volume;
  - for later indices, it clipped the opposite side of the z-axis, which made the visible body look like it was being reduced instead of accumulated.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`
- `frontend/src/vtk/volumeRendering/VolumeRenderer.tsx`

### Frontend Rendering Fix

- `SimulationPage.tsx`
  - changed the CT accumulated 3D renderer to use `syntheticClipDirection="low_to_high"` so head-to-feet playback on the existing `head_to_feet` volume order means visible slices are `0..N`;
  - updated play/restart logic to use the currently active volume depth instead of the original phantom depth, so playback stays aligned with the same simulated stack shown in both views.

- `VolumeRenderer.tsx`
  - replaced the split, direction-specific progressive clipping code with one helper that computes the visible range deterministically;
  - `low_to_high` now always means:
    - `sliceIndex=0` => only the first slice band is visible;
    - `sliceIndex=depth-1` => full volume is visible;
  - `high_to_low` remains supported, but now follows the same stable "keep the visible side only" rule instead of skipping clipping on the first frame.

### Accumulation Principle

- The right-side 3D view still uses VTK clipping planes, not camera/CSS tricks.
- For the CT simulation page, the effective behavior is now:
  - current slice index `N`;
  - preserve only z-slices `0..N`;
  - fully hide `N+1..end`;
  - moving the slider backward reduces visible volume, moving it forward increases visible volume.

### Validation Plan

- Run `npx tsc --noEmit`.
- Browser/manual verification was not executed in this session.

## 2026-07-06 CT 3D Screen-Space Top-To-Bottom Alignment

### Task Scope

- Keep the corrected progressive accumulation set as slices `0..N`.
- Align the 3D camera orientation so head/chest appears toward the top of the CT simulation panel and accumulation reads top-to-bottom on screen.

### Root Cause

- After fixing the clipping plane semantics, the visible slice set was correct, but the default 3D camera still used `viewUp=(0,0,1)`.
- For the current `head_to_feet` z ordering, that made lower z slices appear lower on screen, so the accumulation still looked visually like it was growing from bottom to top.

### Files Modified

- `frontend/src/pages/SimulationPage.tsx`

### Frontend Display Change

- The CT simulation page now passes:
  - `scanView`
  - `scanDirection="head_to_feet"`
- This reuses the existing `VolumeRenderer` camera orientation mode so the already-correct accumulated slice set is displayed in the expected screen-space top-to-bottom direction.

### Validation Plan

- Run `npx tsc --noEmit`.
- Browser/manual verification was not executed in this session.
