import { useState, useRef, useEffect, useCallback } from 'react';
import { Button } from '@components/ui/button';
import { useSimulationStore } from '@store/useSimulationStore';
import { simulationService } from '@/services/simulationService';
import type { PhantomResponse } from '@/services/simulationService';
import { VolumeRenderer } from '@vtk/volumeRendering/VolumeRenderer';

// ---------------------------------------------------------------------------
// Window/Level presets (WL, WW in HU)
// ---------------------------------------------------------------------------

interface WindowPreset {
  label: string;
  windowLevel: number;
  windowWidth: number;
}

const WINDOW_PRESETS: WindowPreset[] = [
  { label: 'Soft', windowLevel: 40, windowWidth: 400 },
  { label: 'Lung', windowLevel: -600, windowWidth: 1500 },
  { label: 'Bone', windowLevel: 500, windowWidth: 2000 },
];

// ---------------------------------------------------------------------------
// Organ color map — RGB 0-255 for label overlay on axial slices
// ---------------------------------------------------------------------------

const ORGAN_COLORS: Record<number, [number, number, number]> = {
  1: [255, 255, 0],   // left_adrenal_gland — yellow
  2: [255, 255, 0],   // right_adrenal_gland — yellow
  3: [139, 69, 19],   // colon — brown
  4: [255, 192, 203], // duodenum — pink
  5: [173, 216, 230], // esophagus — light blue
  6: [0, 255, 0],     // gallbladder — green
  7: [255, 165, 0],   // left_kidney — orange
  8: [255, 165, 0],   // right_kidney — orange
  9: [139, 0, 0],     // liver — dark red
  10: [0, 200, 255],  // left_lung_lower_lobe — cyan
  11: [0, 200, 255],  // right_lung_lower_lobe — cyan
  12: [0, 200, 255],  // right_lung_middle_lobe — cyan
  13: [100, 200, 255],// left_lung_upper_lobe — light cyan
  14: [100, 200, 255],// right_lung_upper_lobe — light cyan
  15: [255, 255, 100],// pancreas — light yellow
  16: [255, 182, 193],// small_bowel — light pink
  17: [128, 0, 128],  // spleen — purple
  18: [210, 180, 140],// stomach — tan
  19: [0, 255, 255],  // trachea — cyan
  20: [0, 0, 255],    // urinary_bladder — blue
};

/** Semi-transparency alpha for organ label overlay (0-1) */
const LABEL_OVERLAY_ALPHA = 0.35;

// ---------------------------------------------------------------------------
// HU → grayscale helper
//    windowLevel = WL, windowWidth = WW
//    visible range: [WL - WW/2,  WL + WW/2]
// ---------------------------------------------------------------------------

function applyWindowLevel(
  hu: number,
  windowLevel: number,
  windowWidth: number,
): number {
  const low = windowLevel - windowWidth / 2;
  const high = windowLevel + windowWidth / 2;
  if (hu <= low) return 0;
  if (hu >= high) return 255;
  return ((hu - low) / windowWidth) * 255;
}

// ---------------------------------------------------------------------------
// Decode base64 → Float32Array
// ---------------------------------------------------------------------------

function decodeBase64ToFloat32(base64: string): Float32Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return new Float32Array(bytes.buffer);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

/**
 * SimulationPage
 *
 * Provides two modes:
 * 1. Lesion/Organ/Deformation simulation (existing) — configures and runs
 *    simulation jobs for lesion/organ synthesis and result export.
 * 2. CT Phantom (new) — generates a synthetic upper-body CT phantom from
 *    the backend, displays axial slices with window/level controls, and
 *    shows a 3D volume preview via vtk.js.
 */
export default function SimulationPage() {
  const { lesions, organs, addLesion, completedJobs } = useSimulationStore();

  // ---- Tab state ----
  const [activeTab, setActiveTab] = useState<
    'lesion' | 'organ' | 'deformation' | 'phantom'
  >('lesion');

  // ---- Export state (lesion/organ mode) ----
  const [exportFormat, setExportFormat] = useState<'dicom' | 'nifti' | 'nrrd'>(
    'dicom',
  );
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);
  const latestCompletedJob = completedJobs[completedJobs.length - 1];

  // -----------------------------------------------------------------------
  // CT Phantom state
  // -----------------------------------------------------------------------

  const [phantom, setPhantom] = useState<PhantomResponse | null>(null);
  const [phantomLoading, setPhantomLoading] = useState(false);
  const [phantomError, setPhantomError] = useState<string | null>(null);
  const [phantomSource, setPhantomSource] = useState<'procedural' | 'atlas'>(
    'procedural',
  );
  const [phantomSize, setPhantomSize] = useState(192); // default 192 for detail
  const [sliceIndex, setSliceIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(10); // slices per second
  const [activePreset, setActivePreset] = useState<WindowPreset>(
    WINDOW_PRESETS[0],
  );
  const [showLabelOverlay, setShowLabelOverlay] = useState(true);

  // ---- vtk.js volume data (decoded once from phantom) ----
  const [vtkVolumeData, setVtkVolumeData] = useState<Float32Array | null>(null);
  const [vtkDims, setVtkDims] = useState<[number, number, number] | null>(null);
  const [vtkSpacing, setVtkSpacing] = useState<[number, number, number] | null>(
    null,
  );

  // ---- Canvas ref for axial slice rendering ----
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // ---- Cached decoded volume data (avoids re-decoding base64 on every
  //      slice / preset change) ----
  const cachedPhantomDataRef = useRef<Float32Array | null>(null);

  // ---- Cached decoded label data (uint8, same [z,y,x] ordering as CT) ----
  const cachedLabelDataRef = useRef<Uint8Array | null>(null);

  // ---- Playback timer ref ----
  const playTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // ---- Reset slice index to 0 when a new phantom is loaded ----
  // This ensures we always start at the head/chest (first slice)
  // rather than showing the last-viewed slice from a previous phantom.
  useEffect(() => {
    if (phantom) {
      setSliceIndex(0);
      setPlaying(false);
    }
  }, [phantom]);

  // -----------------------------------------------------------------------
  // Decode vtk.js data when phantom changes
  // -----------------------------------------------------------------------

  useEffect(() => {
    if (!phantom) {
      setVtkVolumeData(null);
      setVtkDims(null);
      setVtkSpacing(null);
      cachedPhantomDataRef.current = null;
      cachedLabelDataRef.current = null;
      return;
    }
    try {
      const data = decodeBase64ToFloat32(phantom.volumeBase64);
      // Cache the original (z,y,x)-ordered data for slice rendering
      cachedPhantomDataRef.current = data;

      // Decode label data if present
      if (phantom.labelBase64) {
        try {
          const binary = atob(phantom.labelBase64);
          const bytes = new Uint8Array(binary.length);
          for (let i = 0; i < binary.length; i++) {
            bytes[i] = binary.charCodeAt(i);
          }
          cachedLabelDataRef.current = bytes;
        } catch {
          cachedLabelDataRef.current = null;
        }
      } else {
        cachedLabelDataRef.current = null;
      }

      const { width, height, depth, spacing } = phantom.metadata;
      // Volume stored as (z, y, x) in backend; vtk.js expects (x, y, z)
      // so we need to transpose. Build transposed volume: x fastest, then y, then z.
      const xDim = width;
      const yDim = height;
      const zDim = depth;
      const transposed = new Float32Array(xDim * yDim * zDim);
      for (let z = 0; z < zDim; z++) {
        for (let y = 0; y < yDim; y++) {
          for (let x = 0; x < xDim; x++) {
            const srcIdx = z * yDim * xDim + y * xDim + x;
            const dstIdx = x + y * xDim + z * xDim * yDim;
            transposed[dstIdx] = data[srcIdx];
          }
        }
      }
      setVtkVolumeData(transposed);
      setVtkDims([xDim, yDim, zDim]);
      setVtkSpacing([spacing[2], spacing[1], spacing[0]]); // (z,y,x)→(x,y,z)
    } catch (err: any) {
      console.error('[SimulationPage] Failed to decode phantom volume:', err);
      setVtkVolumeData(null);
      setVtkDims(null);
      setVtkSpacing(null);
      cachedPhantomDataRef.current = null;
      cachedLabelDataRef.current = null;
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phantom?.volumeBase64, phantom?.labelBase64]);

  // -----------------------------------------------------------------------
  // Axial slice canvas rendering
  // -----------------------------------------------------------------------

  const renderSlice = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !phantom) return;

    const { width, height, depth } = phantom.metadata;
    // Use cached data to avoid re-decoding base64 on every render
    const data = cachedPhantomDataRef.current;
    if (!data) return;

    if (sliceIndex < 0 || sliceIndex >= depth) return;

    // Extract slice at sliceIndex (z-coordinate)
    const sliceSize = width * height;
    const offset = sliceIndex * sliceSize;
    const slice = data.subarray(offset, offset + sliceSize);

    // Extract label slice if available
    const labelData = cachedLabelDataRef.current;
    const labelSlice =
      labelData && showLabelOverlay
        ? labelData.subarray(offset, offset + sliceSize)
        : null;

    // Render to canvas
    canvas.width = width;
    canvas.height = height;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const imageData = ctx.createImageData(width, height);
    const { windowLevel, windowWidth } = activePreset;

    for (let i = 0; i < sliceSize; i++) {
      const gray = applyWindowLevel(slice[i], windowLevel, windowWidth);
      const pixelIdx = i * 4;

      if (labelSlice && labelSlice[i] > 0) {
        const organColor = ORGAN_COLORS[labelSlice[i]];
        if (organColor) {
          // Blend organ color with grayscale CT
          const alpha = LABEL_OVERLAY_ALPHA;
          imageData.data[pixelIdx] = gray * (1 - alpha) + organColor[0] * alpha;     // R
          imageData.data[pixelIdx + 1] = gray * (1 - alpha) + organColor[1] * alpha; // G
          imageData.data[pixelIdx + 2] = gray * (1 - alpha) + organColor[2] * alpha; // B
          imageData.data[pixelIdx + 3] = 255;  // A
        } else {
          imageData.data[pixelIdx] = gray;
          imageData.data[pixelIdx + 1] = gray;
          imageData.data[pixelIdx + 2] = gray;
          imageData.data[pixelIdx + 3] = 255;
        }
      } else {
        imageData.data[pixelIdx] = gray;     // R
        imageData.data[pixelIdx + 1] = gray; // G
        imageData.data[pixelIdx + 2] = gray; // B
        imageData.data[pixelIdx + 3] = 255;  // A
      }
    }

    ctx.putImageData(imageData, 0, 0);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [phantom, sliceIndex, activePreset, showLabelOverlay]);

  // Re-render on slice/preset change
  useEffect(() => {
    renderSlice();
  }, [renderSlice]);

  // -----------------------------------------------------------------------
  // Playback effect
  // -----------------------------------------------------------------------

  useEffect(() => {
    if (!playing || !phantom) return;

    const intervalMs = 1000 / Math.max(playSpeed, 1);
    playTimerRef.current = setInterval(() => {
      setSliceIndex((prev) => {
        const maxIdx = phantom.metadata.depth - 1;
        if (prev >= maxIdx) {
          // Stop at end (loop disabled by default for MVP)
          setPlaying(false);
          return maxIdx;
        }
        return prev + 1;
      });
    }, intervalMs);

    return () => {
      if (playTimerRef.current) {
        clearInterval(playTimerRef.current);
        playTimerRef.current = null;
      }
    };
  }, [playing, playSpeed, phantom]);

  // -----------------------------------------------------------------------
  // Handlers
  // -----------------------------------------------------------------------

  const handleGeneratePhantom = async () => {
    setPhantomLoading(true);
    setPhantomError(null);
    setPhantom(null);
    setSliceIndex(0);
    setPlaying(false);
    try {
      const size = phantomSource === 'atlas' ? phantomSize : 128;
      const response = await simulationService.getPhantom(
        phantomSource,
        size,
        's0001',
        'head_to_feet',
      );
      setPhantom(response);
    } catch (err: any) {
      console.error('[SimulationPage] Phantom generation failed:', err);
      const detail =
        err?.response?.data?.detail || err.message || 'Failed to generate CT phantom';
      setPhantomError(detail);
    } finally {
      setPhantomLoading(false);
    }
  };

  const handlePlayPause = () => {
    if (!phantom) return;
    if (playing) {
      setPlaying(false);
    } else {
      // If at end, restart from top
      if (sliceIndex >= phantom.metadata.depth - 1) {
        setSliceIndex(0);
      }
      setPlaying(true);
    }
  };

  const handleSliceChange = (value: number) => {
    setSliceIndex(value);
  };

  // -----------------------------------------------------------------------
  // Export helpers (lesion mode)
  // -----------------------------------------------------------------------

  const parseFilename = (headers: any, fallback: string): string => {
    const disposition = headers['content-disposition'];
    if (disposition) {
      const match = disposition.match(/filename\*?=['"]?([^'";]+)['"]?/);
      if (match && match[1]) {
        return decodeURIComponent(match[1]);
      }
    }
    return fallback;
  };

  const triggerDownload = (blob: Blob, filename: string) => {
    const url = window.URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    window.URL.revokeObjectURL(url);
    document.body.removeChild(a);
  };

  const handleExport = async () => {
    if (!latestCompletedJob) {
      setExportError('No completed simulation jobs available for export');
      return;
    }

    setExporting(true);
    setExportProgress(0);
    setExportError(null);

    try {
      const response = await simulationService.exportResults(
        latestCompletedJob.id,
        exportFormat,
        (progressEvent) => {
          if (progressEvent.total) {
            const percent = Math.round(
              (progressEvent.loaded * 100) / progressEvent.total,
            );
            setExportProgress(percent);
          } else {
            setExportProgress(-1);
          }
        },
      );

      const fallbackNames = {
        dicom: `simulation_${latestCompletedJob.id}_dicom.zip`,
        nifti: `simulation_${latestCompletedJob.id}.nii.gz`,
        nrrd: `simulation_${latestCompletedJob.id}.nrrd`,
      };
      const filename = parseFilename(response.headers, fallbackNames[exportFormat]);
      triggerDownload(response.data, filename);
      setExportProgress(100);
    } catch (error: any) {
      const status = error.status;
      let message = 'Export failed';
      if (status === 409) message = 'Simulation job is not completed';
      else if (status === 404) message = 'Simulation job not found';
      else if (status === 400) message = error.message || 'Invalid export request';
      else if (status === 500) message = 'Server error during export';
      else if (error.message) message = error.message;
      setExportError(message);
    } finally {
      setExporting(false);
      setTimeout(() => setExportProgress(0), 1000);
    }
  };

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  const totalSlices = phantom ? phantom.metadata.depth : 0;

  return (
    <div className="flex h-full flex-col">
      {/* ---- Header ---- */}
      <div className="flex-shrink-0 border-b border-border px-6 py-4">
        <h1 className="text-2xl font-bold text-foreground">
          {activeTab === 'phantom'
            ? 'CT Simulation / Synthetic Upper-body CT'
            : 'Lesion Simulation'}
        </h1>
        <p className="text-sm text-muted-foreground">
          {activeTab === 'phantom'
            ? 'Synthetic CT phantom — geometric upper-body anatomy for demo & development'
            : 'Generate synthetic lesions and organs for AI training and validation'}
        </p>
      </div>

      {/* ---- Tab navigation ---- */}
      <div className="flex-shrink-0 flex gap-2 border-b border-border px-6">
        {(['lesion', 'organ', 'deformation', 'phantom'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              activeTab === tab
                ? 'border-primary text-primary'
                : 'border-transparent text-muted-foreground hover:text-foreground'
            }`}
          >
            {tab === 'phantom'
              ? 'CT Phantom'
              : tab.charAt(0).toUpperCase() + tab.slice(1)}
          </button>
        ))}
      </div>

      {/* ---- Tab Content ---- */}
      {activeTab !== 'phantom' ? (
        /* ================================================================ */
        /*  Lesion / Organ / Deformation  (existing UI, preserved)           */
        /* ================================================================ */
        <div className="flex-1 overflow-y-auto p-6">
          {/* Configuration panel */}
          <div className="mb-6 grid gap-6 lg:grid-cols-2">
            {/* Lesion parameters */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="mb-4 text-sm font-medium">Lesion Parameters</h3>
              <div className="space-y-4">
                <div>
                  <label className="text-xs text-muted-foreground">
                    Lesion Type
                  </label>
                  <select className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                    <option value="tumor">Tumor</option>
                    <option value="nodule">Nodule</option>
                    <option value="cyst">Cyst</option>
                    <option value="calcification">Calcification</option>
                    <option value="metastasis">Metastasis</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    Shape
                  </label>
                  <select className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm">
                    <option value="spherical">Spherical</option>
                    <option value="ellipsoidal">Ellipsoidal</option>
                    <option value="irregular">Irregular</option>
                    <option value="lobulated">Lobulated</option>
                    <option value="spiculated">Spiculated</option>
                  </select>
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    Mean HU Value: <span className="text-primary">40</span>
                  </label>
                  <input
                    type="range"
                    min="-1000"
                    max="1000"
                    defaultValue={40}
                    className="mt-1 w-full"
                  />
                </div>
                <div>
                  <label className="text-xs text-muted-foreground">
                    Diameter (mm): <span className="text-primary">20</span>
                  </label>
                  <input
                    type="range"
                    min="2"
                    max="100"
                    defaultValue={20}
                    className="mt-1 w-full"
                  />
                </div>
                <Button onClick={() => addLesion({} as any)} className="w-full">
                  Add Lesion
                </Button>
              </div>
            </div>

            {/* Lesion list */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="mb-4 text-sm font-medium">
                Lesion List ({lesions.length})
              </h3>
              {lesions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No lesions configured. Add lesions using the parameters panel.
                </p>
              ) : (
                <div className="space-y-2">
                  {lesions.map((lesion, index) => (
                    <div
                      key={index}
                      className="rounded border border-border p-2 text-sm"
                    >
                      Lesion {index + 1}
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="flex gap-2 border-t border-border pt-4">
            <Button variant="default">Run Simulation</Button>
            <Button variant="outline">Preview</Button>

            {/* Export section */}
            <div className="flex items-center gap-2">
              <select
                value={exportFormat}
                onChange={(e) =>
                  setExportFormat(e.target.value as 'dicom' | 'nifti' | 'nrrd')
                }
                className="rounded-md border border-border bg-background px-3 py-2 text-sm"
                disabled={exporting}
              >
                <option value="dicom">DICOM</option>
                <option value="nifti">NIfTI</option>
                <option value="nrrd">NRRD</option>
              </select>

              <Button
                variant="ghost"
                onClick={handleExport}
                disabled={exporting}
              >
                {exporting
                  ? exportProgress === -1
                    ? 'Exporting...'
                    : exportProgress > 0 && exportProgress < 100
                      ? `Exporting ${exportProgress}%`
                      : 'Exporting...'
                  : 'Export Results'}
              </Button>
            </div>
          </div>

          {/* Export error */}
          {exportError && (
            <div className="mt-2 text-sm text-destructive">{exportError}</div>
          )}
        </div>
      ) : (
        /* ================================================================ */
        /*  CT Phantom View                                                  */
        /* ================================================================ */
        <div className="flex flex-1 flex-col overflow-hidden">
          {/* ---- Main area: Axial Viewer (left) + 3D Preview (right) ---- */}
          <div className="flex flex-1 overflow-hidden">
            {/* ---- Left: Axial Slice Viewer ---- */}
            <div className="flex w-1/2 flex-col border-r border-border bg-black">
              <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium text-white/70">
                    Axial Slice Viewer
                  </span>
                  {phantom && (
                    <span className="text-[10px] text-cyan-400/70">
                      Scanning: Head/Chest → Abdomen/Pelvis
                    </span>
                  )}
                </div>
                {phantom && (
                  <span className="text-xs tabular-nums text-white/50">
                    Slice {sliceIndex + 1} / {totalSlices}
                  </span>
                )}
              </div>
              <div className="flex-1 flex items-center justify-center p-2">
                {!phantom ? (
                  <div className="flex flex-col items-center gap-3 text-center">
                    <svg
                      className="h-12 w-12 text-white/15"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth={1}
                    >
                      <rect x="2" y="3" width="20" height="14" rx="2" ry="2" />
                      <path d="M8 21h8" />
                      <path d="M12 17v4" />
                    </svg>
                    <p className="text-sm text-white/40">
                      {phantomSource === 'atlas'
                        ? 'Click "Generate Atlas CT" to load the real CT phantom'
                        : 'Click "Generate Synthetic CT" to load the phantom'}
                    </p>
                    {phantomLoading && (
                      <div className="flex items-center gap-2 text-xs text-white/50">
                        <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                        Generating phantom...
                      </div>
                    )}
                    {phantomError && (
                      <p className="text-xs text-red-400">{phantomError}</p>
                    )}
                  </div>
                ) : (
                  <canvas
                    ref={canvasRef}
                    className="max-h-full max-w-full border border-white/10 object-contain"
                    style={{ imageRendering: 'pixelated' }}
                  />
                )}
              </div>
            </div>

            {/* ---- Right: 3D Volume Preview ---- */}
            <div className="flex w-1/2 flex-col bg-black">
              <div className="flex items-center justify-between border-b border-white/10 px-3 py-2">
                <div className="flex flex-col gap-0.5">
                  <span className="text-xs font-medium text-white/70">
                    3D Volume Preview
                  </span>
                  {phantom && (
                    <span className="text-[10px] text-cyan-400/70">
                      Progressive Scan · Head → Feet
                    </span>
                  )}
                </div>
              </div>
              <div className="flex-1">
                {vtkVolumeData && vtkDims ? (
                  <VolumeRenderer
                    mode="synthetic"
                    showControls
                    scanView
                    scanDirection="head_to_feet"
                    opacityPreset={
                      activePreset.label === 'Soft'
                        ? 'ct-soft-tissue'
                        : activePreset.label === 'Lung'
                          ? 'ct-lung'
                          : 'ct-bone'
                    }
                    syntheticData={vtkVolumeData}
                    syntheticDims={vtkDims}
                    syntheticSpacing={vtkSpacing ?? undefined}
                    syntheticClipIndex={sliceIndex}
                    syntheticScanAxis="z"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center">
                    <p className="text-sm text-white/30">
                      Generate a phantom to see 3D preview
                    </p>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* ---- Bottom: Controls bar ---- */}
          <div className="flex-shrink-0 border-t border-border bg-card px-4 py-3">
            <div className="flex items-center gap-4 flex-wrap">
              {/* Source selector */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Source:</span>
                <select
                  value={phantomSource}
                  onChange={(e) => {
                    const src = e.target.value as 'procedural' | 'atlas';
                    setPhantomSource(src);
                    setPhantom(null);
                    setPhantomError(null);
                    // Reset size to sensible default for each mode
                    setPhantomSize(src === 'atlas' ? 192 : 128);
                  }}
                  disabled={phantomLoading}
                  className="rounded border border-border bg-background px-2 py-1 text-xs"
                >
                  <option value="atlas">Real CT Atlas</option>
                  <option value="procedural">Procedural Phantom</option>
                </select>
              </div>

              {/* Size selector */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Size:</span>
                <select
                  value={phantomSize}
                  onChange={(e) => setPhantomSize(Number(e.target.value))}
                  disabled={phantomLoading}
                  className="rounded border border-border bg-background px-2 py-1 text-xs"
                >
                  <option value={96}>96 Fast</option>
                  <option value={160}>160 Balanced</option>
                  <option value={192}>192 Detail</option>
                  <option value={256}>256 Max</option>
                </select>
                <span className="text-[10px] text-muted-foreground/60">
                  larger = clearer, slower
                </span>
              </div>

              {/* Generate button */}
              <Button
                variant="default"
                size="sm"
                onClick={handleGeneratePhantom}
                disabled={phantomLoading}
              >
                {phantomLoading
                  ? 'Generating...'
                  : phantomSource === 'atlas'
                    ? 'Generate Atlas CT'
                    : 'Generate Synthetic CT'}
              </Button>

              {/* Play / Pause */}
              <Button
                variant="outline"
                size="sm"
                onClick={handlePlayPause}
                disabled={!phantom}
              >
                {playing ? '⏸ Pause' : '▶ Play'}
              </Button>

              {/* Slice slider */}
              <div className="flex items-center gap-2 min-w-[200px]">
                <span className="text-xs text-muted-foreground w-16 tabular-nums">
                  {phantom
                    ? `${sliceIndex + 1} / ${totalSlices}`
                    : '— / —'}
                </span>
                <input
                  type="range"
                  min={0}
                  max={Math.max(totalSlices - 1, 0)}
                  step={1}
                  value={sliceIndex}
                  onChange={(e) => handleSliceChange(Number(e.target.value))}
                  disabled={!phantom}
                  className="h-1 flex-1 cursor-pointer accent-primary"
                />
              </div>

              {/* Speed control */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Speed:</span>
                <select
                  value={playSpeed}
                  onChange={(e) => setPlaySpeed(Number(e.target.value))}
                  disabled={!phantom}
                  className="rounded border border-border bg-background px-2 py-1 text-xs"
                >
                  <option value={5}>5 fps</option>
                  <option value={10}>10 fps</option>
                  <option value={20}>20 fps</option>
                  <option value={30}>30 fps</option>
                </select>
              </div>

              <div className="h-5 w-px bg-border" />

              {/* Window preset */}
              <div className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground">Window:</span>
                {WINDOW_PRESETS.map((preset) => (
                  <button
                    key={preset.label}
                    onClick={() => setActivePreset(preset)}
                    className={`rounded px-2 py-1 text-xs transition-colors ${
                      activePreset.label === preset.label
                        ? 'bg-primary text-primary-foreground'
                        : 'bg-muted text-muted-foreground hover:bg-muted-foreground/20'
                    }`}
                    disabled={!phantom}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>

              {/* Preset info */}
              {phantom && (
                <span className="text-xs text-muted-foreground">
                  WL={activePreset.windowLevel} WW={activePreset.windowWidth}
                </span>
              )}

              {/* Label overlay toggle (only when labels available) */}
              {phantom?.labelBase64 && (
                <>
                  <div className="h-5 w-px bg-border" />
                  <label className="flex items-center gap-1.5 text-xs text-muted-foreground cursor-pointer select-none">
                    <input
                      type="checkbox"
                      checked={showLabelOverlay}
                      onChange={(e) => setShowLabelOverlay(e.target.checked)}
                      className="accent-primary"
                    />
                    Organ Labels
                  </label>
                </>
              )}

              {/* Source badge */}
              {phantom && phantom.metadata.source && (
                <>
                  <div className="h-5 w-px bg-border" />
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                    {phantom.metadata.source === 'atlas'
                      ? `atlas · ${phantom.metadata.caseId || '—'}`
                      : 'procedural'}
                  </span>
                </>
              )}

              {/* Scan direction indicator */}
              {phantom && phantom.metadata.scanDirection && (
                <>
                  <div className="h-5 w-px bg-border" />
                  <span className="flex items-center gap-1 text-xs text-muted-foreground">
                    <span className="text-[10px]">
                      {phantom.metadata.scanDirection === 'head_to_feet'
                        ? '🔽 Head → Feet'
                        : '🔼 Feet → Head'}
                    </span>
                    {phantom.metadata.flippedZ && (
                      <span
                        className="rounded bg-amber-500/20 px-1 text-[9px] text-amber-400"
                        title="Volume was flipped along z to match requested scan direction"
                      >
                        flipped
                      </span>
                    )}
                  </span>
                </>
              )}

              {/* Shape info */}
              {phantom && phantom.metadata.outputShape && (
                <>
                  <div className="h-5 w-px bg-border" />
                  <span className="text-[10px] text-muted-foreground/60">
                    shape{' '}
                    {phantom.metadata.outputShape.join('×')}
                  </span>
                </>
              )}
            </div>

            {/* ---- Label legend (compact, shown when labels available) ---- */}
            {phantom?.labelBase64 && showLabelOverlay && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/50 pt-2">
                <span className="text-[10px] text-muted-foreground/60">
                  Labels:
                </span>
                {[
                  [9, 'Liver'],
                  [7, 'L Kidney'],
                  [8, 'R Kidney'],
                  [10, 'Lungs'],
                  [17, 'Spleen'],
                  [15, 'Pancreas'],
                  [19, 'Trachea'],
                  [20, 'Bladder'],
                ].map(([id, name]) => {
                  const color = ORGAN_COLORS[id as number];
                  return (
                    <span
                      key={id}
                      className="flex items-center gap-1 text-[10px] text-muted-foreground"
                    >
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-sm border border-white/20"
                        style={{
                          backgroundColor: color
                            ? `rgb(${color[0]},${color[1]},${color[2]})`
                            : '#888',
                        }}
                      />
                      {name}
                    </span>
                  );
                })}
              </div>
            )}

            {/* ---- Organ slice ranges (atlas debug info) ---- */}
            {phantom?.metadata?.sliceLabelPresence && (
              <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-0.5 border-t border-border/30 pt-1.5">
                <span className="text-[10px] text-muted-foreground/50">
                  Organ z-ranges:
                </span>
                {(
                  [
                    ['lung', 'Lungs'],
                    ['liver', 'Liver'],
                    ['spleen', 'Spleen'],
                    ['kidney_left', 'L Kidney'],
                    ['kidney_right', 'R Kidney'],
                    ['bladder', 'Bladder'],
                  ] as [string, string][]
                ).map(([key, label]) => {
                  const range = phantom.metadata.sliceLabelPresence![key];
                  if (!range) return null;
                  const [zMin, zMax] = range;
                  const total = phantom.metadata.depth;
                  const pct0 = total > 0 ? Math.round((zMin / total) * 100) : 0;
                  const pct1 = total > 0 ? Math.round((zMax / total) * 100) : 0;
                  return (
                    <span
                      key={key}
                      className="text-[10px] text-muted-foreground/70"
                    >
                      {label}: {zMin + 1}–{zMax + 1}{' '}
                      <span className="text-muted-foreground/40">
                        ({pct0}–{pct1}%)
                      </span>
                    </span>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
