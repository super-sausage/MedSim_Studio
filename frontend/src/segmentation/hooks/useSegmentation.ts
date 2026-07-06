/**
 * useSegmentation — Cornerstone3D Segmentation Integration Hook
 *
 * Manages the lifecycle of Cornerstone3D segmentations:
 *   1. Download full 3D NRRD mask from backend when a job completes
 *   2. Parse NRRD → Int32Array of label indices
 *   3. Create a Cornerstone3D labelmap volume derived from the CT volume
 *   4. Register the segmentation with Cornerstone3D's built-in system
 *   5. Provide visibility toggling per segment
 *
 * The SegmentationDisplayTool (registered globally) automatically renders
 * the segmentation overlay on all VolumeViewports in the tool group,
 * correctly aligned with the CT volume.
 *
 * Usage:
 *   const seg = useSegmentation(volumeId);
 *   seg.loadMask(jobId);           // download + register segmentation
 *   seg.setSegmentVisibility(1, false);  // hide liver
 *   seg.setAllSegmentsVisible(true);     // show all
 *   seg.clearMask();                     // unload segmentation
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { volumeLoader, cache } from '@cornerstonejs/core';
import type { Types as CoreTypes } from '@cornerstonejs/core';
import {
  segmentation as csSegmentation,
  Enums as csToolsEnums,
} from '@cornerstonejs/tools';
import { segmentationService } from '@/services/segmentationService';
import { parseNrrd, type NrrdVolume } from '../utils/nrrdParser';
import type { SegmentationLabel } from '@/types/segmentation';
import { useSegmentationStore } from '@/store/useSegmentationStore';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SegmentationState {
  /** Unique segmentation identifier */
  segmentationId: string;
  /** Volume ID of the labelmap volume in Cornerstone3D cache */
  labelmapVolumeId: string;
  /** The representation UID returned by Cornerstone3D */
  representationUID: string;
  /** Parsed label definitions with colors */
  labels: SegmentationLabel[];
  /** Whether the mask data has been loaded and registered */
  loaded: boolean;
  /** Raw label map data (z,y,x order) — for 3D volume rendering */
  maskData: Float32Array | null;
}

/**
 * RGB color with alpha (0-255 each channel)
 */
type RGBA = [number, number, number, number];

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

/** Default segmentation ID prefix — combined with jobId */
const SEGMENTATION_ID_PREFIX = 'seg-cs';

/** Default tool group ID used for segmentation rendering */
const TOOL_GROUP_ID = 'ct-tool-group';

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSegmentation(ctVolumeId: string | undefined) {
  const [segState, setSegState] = useState<SegmentationState | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Guard against stale async operations
  const cancelledRef = useRef(false);
  const segStateRef = useRef(segState);
  segStateRef.current = segState;

  // Clean up on unmount or volume change
  useEffect(() => {
    return () => {
      cancelledRef.current = true;
    };
  }, []);

  /**
   * Load a completed segmentation mask:
   * 1. Download NRRD from backend
   * 2. Parse into label array
   * 3. Create Cornerstone3D labelmap volume
   * 4. Register with Cornerstone3D segmentation system
   */
  const loadMask = useCallback(
    async (jobId: string, labels: SegmentationLabel[], seriesId?: string) => {
      if (!ctVolumeId) {
        setError('No CT volume ID available — cannot load segmentation');
        return;
      }

      // Clear any previous segmentation first
      const prevState = segStateRef.current;
      if (prevState) {
        await clearSegmentation(prevState.representationUID);
      }

      cancelledRef.current = false;
      setLoading(true);
      setError(null);

      try {
        // ---- Step 1: Check the source volume exists ----
        const sourceVolume = cache.getVolume(ctVolumeId);
        if (!sourceVolume) {
          throw new Error(
            `CT volume "${ctVolumeId}" not found in cache. ` +
            'Ensure the volume is loaded before calling loadMask().'
          );
        }

        const dimensions = sourceVolume.dimensions; // [x, y, z]
        const spacing = sourceVolume.spacing;         // [x, y, z]
        const origin = sourceVolume.origin;           // [x, y, z]
        const direction = sourceVolume.direction;     // Float64Array(9)

        // ---- Step 2: Download NRRD ----
        const response = await segmentationService.downloadMask(jobId);
        const nrrdBlob = response.data as Blob;
        const nrrdBuffer = await nrrdBlob.arrayBuffer();

        // ---- Step 3: Parse NRRD ----
        const nrrd: NrrdVolume = await parseNrrd(nrrdBuffer);
        if (cancelledRef.current) return;

        const numVoxels = nrrd.data.length;
        const expectedVoxels = dimensions[0] * dimensions[1] * dimensions[2];
        if (numVoxels !== expectedVoxels) {
          console.warn(
            `[useSegmentation] Mask data size mismatch: ` +
            `NRRD has ${numVoxels} voxels, CT volume has ${expectedVoxels}. ` +
            `Attempting to proceed, but alignment may be incorrect.`
          );
        }

        // ---- Step 3b: Downsample labelmap if needed ----
        // Large labelmap volumes (>8M voxels) cause WebGL context loss on
        // integrated GPUs. Downsample using nearest-neighbor to keep GPU
        // texture size manageable while preserving label boundaries.
        const segLoadId = `${SEGMENTATION_ID_PREFIX}-${jobId}-${Date.now()}`;
        const segmentationId = segLoadId;
        const totalVoxels = dimensions[0] * dimensions[1] * dimensions[2];
        const needsDownsample =
          totalVoxels > MAX_LABELMAP_VOXELS ||
          dimensions[0] > MAX_LABELMAP_DIM ||
          dimensions[1] > MAX_LABELMAP_DIM;

        let labelmapVolumeId = `labelmap-${segLoadId}`;
        let labelmapVolume: any;
        let finalMaskBytes: Uint8Array | null = null;
        let maskDimensions: [number, number, number] | undefined;
        let maskSpacing: [number, number, number] | undefined;

        if (needsDownsample) {
          // Calculate target dimensions preserving aspect ratio
          const scale = MAX_LABELMAP_DIM / Math.max(dimensions[0], dimensions[1]);
          const td: [number, number, number] = [
            Math.max(1, Math.round(dimensions[0] * scale)),
            Math.max(1, Math.round(dimensions[1] * scale)),
            Math.max(1, Math.round(dimensions[2] * scale)),
          ];
          // Apply secondary voxel-count cap to handle very deep volumes
          const scaledVoxels = td[0] * td[1] * td[2];
          if (scaledVoxels > MAX_LABELMAP_VOXELS) {
            const vscale = Math.cbrt(MAX_LABELMAP_VOXELS / scaledVoxels);
            td[0] = Math.max(1, Math.round(td[0] * vscale));
            td[1] = Math.max(1, Math.round(td[1] * vscale));
            td[2] = Math.max(1, Math.round(td[2] * vscale));
          }

          maskDimensions = td;
          maskSpacing = [
            spacing[0] * (dimensions[0] / td[0]),
            spacing[1] * (dimensions[1] / td[1]),
            spacing[2] * (dimensions[2] / td[2]),
          ] as [number, number, number];

          // Downsample using nearest neighbor to preserve label integers
          finalMaskBytes = downsampleLabelmap(
            nrrd.data,
            dimensions,
            td,
          );

          // Create a downsampled labelmap volume (Uint8 → saves GPU memory)
          labelmapVolume = await (
            volumeLoader as any
          ).createLocalSegmentationVolume(
            {
              dimensions: td,
              spacing: maskSpacing,
              origin,
              direction,
              scalarData: finalMaskBytes,
              metadata: (sourceVolume as any).metadata ?? {},
              referencedVolumeId: ctVolumeId,
            },
            labelmapVolumeId,
          );

          if (cancelledRef.current) {
            cache.removeVolumeLoadObject(labelmapVolumeId);
            return;
          }

          console.info(
            `[useSegmentation] Downsampled labelmap ` +
            `${dimensions[0]}×${dimensions[1]}×${dimensions[2]} → ` +
            `${td[0]}×${td[1]}×${td[2]} ` +
            `(${((td[0]*td[1]*td[2])/(totalVoxels)*100).toFixed(1)}%)`
          );
        } else {
          // ---- Step 4 (full-res path): Create derived volume ----
          labelmapVolume = await volumeLoader.createAndCacheDerivedVolume(
            ctVolumeId,
            { volumeId: labelmapVolumeId },
          );
          if (cancelledRef.current) {
            cache.removeVolumeLoadObject(labelmapVolumeId);
            return;
          }

          // Fill with parsed label data
          const scalarData = labelmapVolume.getScalarData();
          if (scalarData) {
            scalarData.set(nrrd.data);
          }
          labelmapVolume.modified();
        }

        // ---- Step 5: Register with Cornerstone3D segmentation system ----
        // First, add the segmentation to global state
        csSegmentation.addSegmentations([
          {
            segmentationId,
            representation: {
              type: csToolsEnums.SegmentationRepresentations.Labelmap,
              data: {
                volumeId: labelmapVolumeId,
              },
            },
          },
        ]);

        // Add the representation to the tool group so it renders
        const representationUIDs =
          await csSegmentation.addSegmentationRepresentations(
            TOOL_GROUP_ID,
            [
              {
                segmentationId,
                type: csToolsEnums.SegmentationRepresentations.Labelmap,
              },
            ],
          );

        const representationUID = representationUIDs?.[0] ?? '';

        // ---- Step 6: Apply label colors ----
        // Set color for each segment individually using the per-segment API.
        // Cornerstone3D expects colors in [0-255] range (see CORNERSTONE_COLOR_LUT).
        for (const label of labels) {
          const [r, g, b] = label.color;
          const color: CoreTypes.Color = [r, g, b, 255]; // [R, G, B, A] in 0-255
          csSegmentation.config.color.setColorForSegmentIndex(
            TOOL_GROUP_ID,
            representationUID,
            label.index,
            color,
          );
        }
        // Make background transparent
        csSegmentation.config.color.setColorForSegmentIndex(
          TOOL_GROUP_ID,
          representationUID,
          0,
          [0, 0, 0, 0],
        );

        // ---- Step 7: Set initial segment visibility ----
        // Set all segments visible by default
        for (const label of labels) {
          csSegmentation.config.visibility.setSegmentVisibility(
            TOOL_GROUP_ID,
            representationUID,
            label.index,
            true,
          );
        }

        if (cancelledRef.current) {
          csSegmentation.removeSegmentationsFromToolGroup(TOOL_GROUP_ID, [representationUID]);
          return;
        }

        // Convert to Float32Array for VTK 3D volume rendering / zustand store
        const maskFloat = new Float32Array(
          finalMaskBytes ?? nrrd.data,
        );

        const segStateVal = {
          segmentationId,
          labelmapVolumeId,
          representationUID,
          labels,
          loaded: true,
          maskData: maskFloat,
        };
        setSegState(segStateVal);

        // Persist to zustand store so mask data survives page navigation
        useSegmentationStore.getState().setPersistedMask({
          jobId,
          seriesId: seriesId ?? useSegmentationStore.getState().activeSeriesId ?? '',
          maskData: maskFloat,
          labels,
          ...(maskDimensions && maskSpacing
            ? { maskDimensions, maskSpacing }
            : {}),
        });

        setLoading(false);
        const displayVoxels = finalMaskBytes
          ? (maskDimensions![0] * maskDimensions![1] * maskDimensions![2])
          : numVoxels;
        console.info(
          `[useSegmentation] Loaded mask for job ${jobId}: ` +
          `${labels.length} segments, ${displayVoxels} voxels` +
          (finalMaskBytes ? ` (downsampled from ${numVoxels})` : '')
        );
      } catch (err: any) {
        if (!cancelledRef.current) {
          const msg = err?.message ?? 'Failed to load segmentation mask';
          setError(msg);
          setLoading(false);
          console.error('[useSegmentation]', msg);
        }
      }
    },
    [ctVolumeId],
  );

  /**
   * Restore a segmentation mask from cached data in the zustand store.
   *
   * This is identical to `loadMask` but uses previously-downloaded mask data
   * (Float32Array) instead of re-fetching the NRRD from the backend.
   * Called when the user navigates back to the Segmentation page after
   * having already completed a segmentation run.
   */
  const restoreMaskFromCache = useCallback(
    async (cached: {
      jobId: string;
      maskData: Float32Array;
      labels: SegmentationLabel[];
      maskDimensions?: [number, number, number];
      maskSpacing?: [number, number, number];
    }) => {
      if (!ctVolumeId) {
        setError('No CT volume ID available — cannot restore segmentation');
        return;
      }

      // Clear any previous segmentation first
      const prevState = segStateRef.current;
      if (prevState) {
        await clearSegmentation(prevState.representationUID);
      }

      cancelledRef.current = false;
      setLoading(true);
      setError(null);

      try {
        // ---- Step 1: Check the source volume exists ----
        const sourceVolume = cache.getVolume(ctVolumeId);
        if (!sourceVolume) {
          throw new Error(
            `CT volume "${ctVolumeId}" not found in cache. ` +
            'Ensure the volume is loaded before calling restoreMaskFromCache().'
          );
        }

        const dimensions = sourceVolume.dimensions;
        const spacing = sourceVolume.spacing;
        const origin = sourceVolume.origin;
        const direction = sourceVolume.direction;

        const numVoxels = cached.maskData.length;
        // If this is a downsampled mask, validate against stored dimensions;
        // otherwise validate against CT volume dimensions.
        const expectedVoxels = cached.maskDimensions
          ? cached.maskDimensions[0] * cached.maskDimensions[1] * cached.maskDimensions[2]
          : dimensions[0] * dimensions[1] * dimensions[2];
        if (numVoxels !== expectedVoxels) {
          console.warn(
            `[useSegmentation] Cached mask data size mismatch: ` +
            `${numVoxels} voxels, expected ${expectedVoxels}. ` +
            `Attempting to proceed, but alignment may be incorrect.`
          );
        }

        // ---- Step 2: Create labelmap volume ----
        const segLoadId = `${SEGMENTATION_ID_PREFIX}-cached-${cached.jobId}-${Date.now()}`;
        const segmentationId = segLoadId;
        const labelmapVolumeId = `labelmap-${segLoadId}`;

        let labelmapVolume: any;

        if (cached.maskDimensions && cached.maskSpacing) {
          // Restore downsampled mask — create local volume at stored resolution
          const maskBytes = new Uint8Array(cached.maskData);
          labelmapVolume = await (
            volumeLoader as any
          ).createLocalSegmentationVolume(
            {
              dimensions: cached.maskDimensions,
              spacing: cached.maskSpacing,
              origin,
              direction,
              scalarData: maskBytes,
              metadata: (sourceVolume as any).metadata ?? {},
              referencedVolumeId: ctVolumeId,
            },
            labelmapVolumeId,
          );
        } else {
          // Restore full-resolution mask — create derived volume
          labelmapVolume = await volumeLoader.createAndCacheDerivedVolume(
            ctVolumeId,
            { volumeId: labelmapVolumeId }
          );
          if (cancelledRef.current) {
            cache.removeVolumeLoadObject(labelmapVolumeId);
            return;
          }

          // Fill with cached label data
          const scalarData = labelmapVolume.getScalarData();
          if (scalarData) {
            scalarData.set(cached.maskData);
          }
          labelmapVolume.modified();
        }

        // ---- Step 3: Register with Cornerstone3D segmentation system ----
        csSegmentation.addSegmentations([
          {
            segmentationId,
            representation: {
              type: csToolsEnums.SegmentationRepresentations.Labelmap,
              data: {
                volumeId: labelmapVolumeId,
              },
            },
          },
        ]);

        const representationUIDs =
          await csSegmentation.addSegmentationRepresentations(
            TOOL_GROUP_ID,
            [
              {
                segmentationId,
                type: csToolsEnums.SegmentationRepresentations.Labelmap,
              },
            ],
          );

        const representationUID = representationUIDs?.[0] ?? '';

        // ---- Step 4: Apply label colors ----
        for (const label of cached.labels) {
          const [r, g, b] = label.color;
          csSegmentation.config.color.setColorForSegmentIndex(
            TOOL_GROUP_ID,
            representationUID,
            label.index,
            [r, g, b, 255],
          );
        }
        csSegmentation.config.color.setColorForSegmentIndex(
          TOOL_GROUP_ID,
          representationUID,
          0,
          [0, 0, 0, 0],
        );

        // ---- Step 5: Set initial segment visibility ----
        for (const label of cached.labels) {
          csSegmentation.config.visibility.setSegmentVisibility(
            TOOL_GROUP_ID,
            representationUID,
            label.index,
            true,
          );
        }

        if (cancelledRef.current) {
          csSegmentation.removeSegmentationsFromToolGroup(TOOL_GROUP_ID, [representationUID]);
          return;
        }

        const segStateVal = {
          segmentationId,
          labelmapVolumeId,
          representationUID,
          labels: cached.labels,
          loaded: true,
          maskData: cached.maskData,
        };
        setSegState(segStateVal);

        setLoading(false);
        console.info(
          `[useSegmentation] Restored mask for job ${cached.jobId} from cache: ` +
          `${cached.labels.length} segments, ${numVoxels} voxels`
        );
      } catch (err: any) {
        if (!cancelledRef.current) {
          const msg = err?.message ?? 'Failed to restore segmentation from cache';
          setError(msg);
          setLoading(false);
          console.error('[useSegmentation]', msg);
        }
      }
    },
    [ctVolumeId],
  );

  /**
   * Toggle visibility of a single segment (organ).
   */
  const setSegmentVisibility = useCallback(
    (segmentIndex: number, visible: boolean) => {
      const state = segStateRef.current;
      if (!state?.loaded) return;
      csSegmentation.config.visibility.setSegmentVisibility(
        TOOL_GROUP_ID,
        state.representationUID,
        segmentIndex,
        visible,
      );
    },
    [],
  );

  /**
   * Set all segments to visible or invisible.
   */
  const setAllSegmentsVisible = useCallback(
    (visible: boolean) => {
      const state = segStateRef.current;
      if (!state?.loaded) return;
      for (const label of state.labels) {
        if (label.index === 0) continue; // skip background
        csSegmentation.config.visibility.setSegmentVisibility(
          TOOL_GROUP_ID,
          state.representationUID,
          label.index,
          visible,
        );
      }
    },
    [],
  );

  /**
   * Update visibility from a Set of visible label indices.
   */
  const syncVisibilityFromSet = useCallback(
    (visibleSet: Set<number>) => {
      const state = segStateRef.current;
      if (!state?.loaded) return;
      for (const label of state.labels) {
        if (label.index === 0) continue;
        csSegmentation.config.visibility.setSegmentVisibility(
          TOOL_GROUP_ID,
          state.representationUID,
          label.index,
          visibleSet.has(label.index),
        );
      }
    },
    [],
  );

  /**
   * Remove the segmentation from Cornerstone3D and free resources.
   *
   * NOTE: Does NOT clear the persisted mask in the zustand store —
   *       that is left to the component so the data survives page
   *       navigation / tab switches. Call clearPersistedMask() from
   *       the store directly when the user explicitly clears or picks
   *       a different series.
   */
  const clearMask = useCallback(async () => {
    const state = segStateRef.current;
    if (state) {
      csSegmentation.removeSegmentationsFromToolGroup(TOOL_GROUP_ID, [state.representationUID]);
    }
    setSegState(null);
    setError(null);
  }, []);

  // Clean up when ctVolumeId changes
  useEffect(() => {
    if (!ctVolumeId && segState) {
      clearMask();
    }
  }, [ctVolumeId, segState, clearMask]);

  return {
    /** Current segmentation state (null if none loaded) */
    segState,
    /** True while downloading/parsing */
    loading,
    /** Error message if load failed */
    error,
    /** Load a completed job's mask */
    loadMask,
    /** Restore a previously cached mask (survives tab switches) */
    restoreMaskFromCache,
    /** Toggle a single label index on/off */
    setSegmentVisibility,
    /** Make all segments visible or hidden */
    setAllSegmentsVisible,
    /** Sync visibility from a Set<number> of visible label indices */
    syncVisibilityFromSet,
    /** Remove segmentation and free resources */
    clearMask,
    /** Raw label map as Float32Array for 3D volume rendering (null if not loaded) */
    maskData: segState?.maskData ?? null,
  };
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

/**
 * Remove a segmentation from Cornerstone3D and clean up its labelmap volume.
 */
async function clearSegmentation(representationUID: string): Promise<void> {
  try {
    // Remove the segmentation representation from the tool group
    csSegmentation.removeSegmentationsFromToolGroup(TOOL_GROUP_ID, [representationUID]);
  } catch {
    // May already be removed — ignore
  }
}

// ---------------------------------------------------------------------------
// Labelmap downsampling
// ---------------------------------------------------------------------------

/**
 * Maximum number of voxels for labelmap volumes uploaded to GPU.
 * Above this threshold we downsample to prevent WebGL context loss
 * on integrated GPUs.
 *
 * 8M voxels ≈ 256×256×128 ≈ 32MB as Float32 texture → safe for most GPUs.
 * At Uint8 (used internally by createLocalSegmentationVolume) it's only 8MB.
 */
const MAX_LABELMAP_VOXELS = 8_000_000;

/**
 * Maximum dimension (width, height, depth) for any axis of the labelmap.
 * The XY plane is the primary concern — Z is usually much smaller.
 */
const MAX_LABELMAP_DIM = 256;

/**
 * Nearest-neighbor downsampling of a 3D labelmap volume.
 * Preserves integer label values (no interpolation).
 *
 * @param src - Source label data (Int32Array or Float32Array), C-contiguous (x varies fastest)
 * @param srcDims - Source dimensions [x, y, z]
 * @param dstDims - Target dimensions [x, y, z]
 * @returns Uint8Array of downsampled data
 */
function downsampleLabelmap(
  src: Int32Array | Float32Array,
  srcDims: readonly number[],
  dstDims: readonly number[],
): Uint8Array {
  const [sx, sy, sz] = srcDims;
  const [dx, dy, dz] = dstDims;
  const rx = sx / dx;
  const ry = sy / dy;
  const rz = sz / dz;
  const out = new Uint8Array(dx * dy * dz);

  for (let z = 0; z < dz; z++) {
    const srcZ = Math.min(Math.round(z * rz), sz - 1);
    const zOffsetSrc = srcZ * sx * sy;
    const zOffsetDst = z * dx * dy;
    for (let y = 0; y < dy; y++) {
      const srcY = Math.min(Math.round(y * ry), sy - 1);
      const yOffsetSrc = zOffsetSrc + srcY * sx;
      const yOffsetDst = zOffsetDst + y * dx;
      for (let x = 0; x < dx; x++) {
        const srcX = Math.min(Math.round(x * rx), sx - 1);
        out[yOffsetDst + x] = src[yOffsetSrc + srcX];
      }
    }
  }

  return out;
}
