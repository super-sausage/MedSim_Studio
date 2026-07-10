import { useState, useRef, useEffect, useCallback, useMemo } from 'react';
import { Button } from '@components/ui/button';
import { useSimulationStore } from '@store/useSimulationStore';
import { simulationService } from '@/services/simulationService';
import type { PhantomResponse, DicomLesionPreviewResponse } from '@/services/simulationService';
import { VolumeRenderer } from '@vtk/volumeRendering/VolumeRenderer';
import { dicomService } from '@/services/dicomService';
import type {
  AtlasCaseOption,
  CtParamsPreviewParams,
  CtParamsPreviewResponse,
  Lesion3DPreviewRequest,
  Lesion3DPreviewResponse,
  LesionInPhantomPreviewResponse,
  DicomLesion3DPreviewResponse,
  LesionMeshData,
  LesionConfig,
  LesionType,
  LesionShape,
} from '@/types/simulation';
import type { DicomStudy, DicomSeries } from '@/types/dicom';

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
  1: [250, 233, 143],  // left_adrenal_gland
  2: [250, 233, 143],  // right_adrenal_gland
  3: [205, 156, 130],  // colon
  4: [246, 196, 206],  // duodenum
  5: [177, 219, 242],  // esophagus
  6: [168, 224, 162],  // gallbladder
  7: [255, 204, 143],  // left_kidney
  8: [255, 204, 143],  // right_kidney
  9: [223, 143, 128],  // liver
  10: [136, 221, 235], // left_lung_lower_lobe
  11: [136, 221, 235], // right_lung_lower_lobe
  12: [136, 221, 235], // right_lung_middle_lobe
  13: [181, 237, 245], // left_lung_upper_lobe
  14: [181, 237, 245], // right_lung_upper_lobe
  15: [248, 226, 141], // pancreas
  16: [239, 198, 213], // small_bowel
  17: [198, 160, 218], // spleen
  18: [220, 191, 166], // stomach
  19: [199, 241, 255], // trachea
  20: [147, 177, 241], // urinary_bladder
  21: [246, 244, 236], // spinal_cord
  100: [255, 84, 84],  // neoplasm_primary_gtv
};

/** Semi-transparency alpha for organ label overlay (0-1) */
const LABEL_OVERLAY_ALPHA = 0.35;
const LESION_LABEL_BASE = 100;

const ORGAN_LABEL_PRIORITY = [100, 13, 14, 21, 9, 7, 8, 10, 11, 12, 17, 15, 19, 20, 18, 6, 5, 3, 4, 16, 1, 2];

function formatOrganLabelName(name: string): string {
  return name
    .split('_')
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(' ');
}

// ---------------------------------------------------------------------------
// HU → grayscale helper
//    windowLevel = WL, windowWidth = WW
//    visible range: [WL - WW/2,  WL + WW/2]
// ---------------------------------------------------------------------------

interface SlicePositionIndicatorProps {
  sliceIndex: number;
  scanStartIndex: number;
  scanEndIndex: number;
}

/** Compact shoulder-to-waist guide for relating the axial image to its scan height. */
function SlicePositionIndicator({
  sliceIndex,
  scanStartIndex,
  scanEndIndex,
}: SlicePositionIndicatorProps) {
  const scanSpan = Math.max(scanEndIndex - scanStartIndex, 1);
  const progress = Math.max(0, Math.min(1, (sliceIndex - scanStartIndex) / scanSpan));
  const lineY = 38 + progress * 100;

  return (
    <div
      className="pointer-events-none absolute right-3 top-3 z-10 rounded-xl border border-white/10 bg-slate-950/85 px-2.5 py-2 shadow-lg backdrop-blur-sm"
      aria-label={`Current axial slice ${sliceIndex + 1}`}
    >
      <div className="mb-1 text-center text-[9px] font-medium uppercase tracking-[0.16em] text-slate-300/65">
        Slice position
      </div>
      <svg viewBox="0 0 88 160" className="h-36 w-[78px]" role="img" aria-hidden="true">
        <defs>
          <linearGradient id="torso-guide-fill" x1="0" x2="1" y1="0" y2="1">
            <stop offset="0" stopColor="#dbeafe" stopOpacity="0.9" />
            <stop offset="1" stopColor="#67e8f9" stopOpacity="0.55" />
          </linearGradient>
        </defs>
        <path
          d="M25 31c-8 1-14 7-17 16L4 65l13 4 7-15v86h40V54l7 15 13-4-4-18c-3-9-9-15-17-16l-9 3H34l-9-3Z"
          fill="url(#torso-guide-fill)"
        />
        <path d="M44 36v82M28 80h32" fill="none" stroke="#0f172a" strokeOpacity="0.28" strokeWidth="1" />
        <path d="M24 140h40" fill="none" stroke="#a5f3fc" strokeLinecap="round" strokeWidth="3" />
        <text x="44" y="20" textAnchor="middle" fill="#cbd5e1" fontSize="8">SHOULDERS</text>
        <text x="44" y="156" textAnchor="middle" fill="#cbd5e1" fontSize="8">WAIST</text>
        <line x1="8" y1={lineY} x2="80" y2={lineY} stroke="#fb7185" strokeWidth="2.5" />
        <circle cx="8" cy={lineY} r="2.5" fill="#fb7185" />
        <circle cx="80" cy={lineY} r="2.5" fill="#fb7185" />
      </svg>
      <div className="mt-0.5 text-center text-[10px] tabular-nums text-rose-200/90">#{sliceIndex + 1}</div>
    </div>
  );
}

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

function decodeBase64ToUint8(base64: string): Uint8Array {
  const binary = atob(base64);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    bytes[i] = binary.charCodeAt(i);
  }
  return bytes;
}

function buildVtkVolumePayload(
  volumeData: Float32Array,
  shape: [number, number, number],
  spacing: [number, number, number],
): {
  transposedData: Float32Array;
  dims: [number, number, number];
  vtkSpacing: [number, number, number];
} {
  const [depth, height, width] = shape;
  const transposed = new Float32Array(width * height * depth);

  for (let z = 0; z < depth; z++) {
    for (let y = 0; y < height; y++) {
      for (let x = 0; x < width; x++) {
        const srcIdx = z * height * width + y * width + x;
        const dstIdx = x + y * width + z * width * height;
        transposed[dstIdx] = volumeData[srcIdx];
      }
    }
  }

  return {
    transposedData: transposed,
    dims: [width, height, depth],
    vtkSpacing: [spacing[2], spacing[1], spacing[0]],
  };
}

function getCenteredVolumeOriginMm(
  shape: [number, number, number],
  spacing: [number, number, number],
): [number, number, number] {
  const [depth, height, width] = shape;
  const [spz, spy, spx] = spacing;

  return [
    -((width - 1) * spx) / 2,
    -((height - 1) * spy) / 2,
    -((depth - 1) * spz) / 2,
  ];
}

function voxelToCenteredWorldMm(
  voxel: [number, number, number],
  shape: [number, number, number],
  spacing: [number, number, number],
): [number, number, number] {
  const [x, y, z] = voxel;
  const [spz, spy, spx] = spacing;
  const [originX, originY, originZ] = getCenteredVolumeOriginMm(shape, spacing);

  return [
    originX + x * spx,
    originY + y * spy,
    originZ + z * spz,
  ];
}

function centeredWorldMmToVoxel(
  world: [number, number, number],
  shape: [number, number, number],
  spacing: [number, number, number],
): [number, number, number] {
  const [worldX, worldY, worldZ] = world;
  const [spz, spy, spx] = spacing;
  const [originX, originY, originZ] = getCenteredVolumeOriginMm(shape, spacing);

  return [
    (worldX - originX) / spx,
    (worldY - originY) / spy,
    (worldZ - originZ) / spz,
  ];
}

function findInformativeSliceRange(
  volumeData: Float32Array,
  shape: [number, number, number],
  bodyThresholdHU = -500,
): { startIndex: number; endIndex: number } {
  const [depth, height, width] = shape;
  const sliceSize = width * height;
  const minBodyPixels = Math.max(16, Math.floor(sliceSize * 0.001));
  const minConsecutiveSlices = Math.min(3, depth);
  const bodyPixelCounts = new Uint32Array(depth);
  let startIndex = 0;
  let endIndex = Math.max(depth - 1, 0);

  for (let z = 0; z < depth; z++) {
    const offset = z * sliceSize;
    let bodyPixels = 0;
    for (let i = 0; i < sliceSize; i++) {
      if (volumeData[offset + i] >= bodyThresholdHU) {
        bodyPixels += 1;
        if (bodyPixels >= minBodyPixels) {
          startIndex = z;
          z = depth;
          break;
        }
      }
    }
    bodyPixelCounts[z] = bodyPixels;
  }

  for (let z = 0; z <= depth - minConsecutiveSlices; z++) {
    let sustained = true;
    for (let k = 0; k < minConsecutiveSlices; k++) {
      if (bodyPixelCounts[z + k] < minBodyPixels) {
        sustained = false;
        break;
      }
    }
    if (sustained) {
      startIndex = z;
      break;
    }
  }

  for (let z = depth - 1; z >= startIndex + minConsecutiveSlices - 1; z--) {
    let sustained = true;
    for (let k = 0; k < minConsecutiveSlices; k++) {
      if (bodyPixelCounts[z - k] < minBodyPixels) {
        sustained = false;
        break;
      }
    }
    if (sustained) {
      endIndex = z;
      return { startIndex, endIndex };
    }
  }

  return { startIndex: 0, endIndex: Math.max(depth - 1, 0) };
}

function renderVolumeSliceToCanvas({
  canvas,
  width,
  height,
  depth,
  volumeData,
  sliceIndex,
  windowLevel,
  windowWidth,
  labelData,
  showLabelOverlay = false,
  pickedPosition,
}: {
  canvas: HTMLCanvasElement;
  width: number;
  height: number;
  depth: number;
  volumeData: Float32Array;
  sliceIndex: number;
  windowLevel: number;
  windowWidth: number;
  labelData?: Uint8Array | null;
  showLabelOverlay?: boolean;
  pickedPosition?: { x: number; y: number; z: number } | null;
}): void {
  if (sliceIndex < 0 || sliceIndex >= depth) return;

  const sliceSize = width * height;
  const offset = sliceIndex * sliceSize;
  const slice = volumeData.subarray(offset, offset + sliceSize);
  const labelSlice =
    labelData && showLabelOverlay
      ? labelData.subarray(offset, offset + sliceSize)
      : null;

  canvas.width = width;
  canvas.height = height;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;

  const imageData = ctx.createImageData(width, height);

  for (let i = 0; i < sliceSize; i++) {
    const gray = applyWindowLevel(slice[i], windowLevel, windowWidth);
    const pixelIdx = i * 4;

    if (labelSlice && labelSlice[i] > 0) {
      const organColor = ORGAN_COLORS[labelSlice[i]];
      if (organColor) {
        const alpha = LABEL_OVERLAY_ALPHA;
        imageData.data[pixelIdx] = gray * (1 - alpha) + organColor[0] * alpha;
        imageData.data[pixelIdx + 1] = gray * (1 - alpha) + organColor[1] * alpha;
        imageData.data[pixelIdx + 2] = gray * (1 - alpha) + organColor[2] * alpha;
        imageData.data[pixelIdx + 3] = 255;
      } else {
        imageData.data[pixelIdx] = gray;
        imageData.data[pixelIdx + 1] = gray;
        imageData.data[pixelIdx + 2] = gray;
        imageData.data[pixelIdx + 3] = 255;
      }
    } else {
      imageData.data[pixelIdx] = gray;
      imageData.data[pixelIdx + 1] = gray;
      imageData.data[pixelIdx + 2] = gray;
      imageData.data[pixelIdx + 3] = 255;
    }
  }

  ctx.putImageData(imageData, 0, 0);

  if (pickedPosition && pickedPosition.z === sliceIndex) {
    const cx = pickedPosition.x;
    const cy = pickedPosition.y;
    const size = 12;

    ctx.save();
    ctx.strokeStyle = '#ff3333';
    ctx.lineWidth = 2;
    ctx.shadowColor = 'rgba(0,0,0,0.8)';
    ctx.shadowBlur = 3;

    ctx.beginPath();
    ctx.moveTo(cx - size, cy);
    ctx.lineTo(cx + size, cy);
    ctx.stroke();

    ctx.beginPath();
    ctx.moveTo(cx, cy - size);
    ctx.lineTo(cx, cy + size);
    ctx.stroke();

    ctx.beginPath();
    ctx.arc(cx, cy, size + 4, 0, 2 * Math.PI);
    ctx.stroke();

    ctx.restore();
  }
}

// ---------------------------------------------------------------------------
// Default form values
// ---------------------------------------------------------------------------

const DEFAULT_FORM: {
  lesionType: LesionType;
  shape: LesionShape;
  huMean: number;
  huStd: number;
  diameter: number;
  marginSharpness: number;
  spiculationDegree: number;
  centerX: number;
  centerY: number;
  centerZ: number;
  normalizedCenterX: number;
  normalizedCenterY: number;
  normalizedCenterZ: number;
} = {
  lesionType: 'tumor',
  shape: 'spherical',
  huMean: 40,
  huStd: 20,
  diameter: 20,
  marginSharpness: 0.8,
  spiculationDegree: 0,
  centerX: 0,
  centerY: 0,
  centerZ: 0,
  normalizedCenterX: 0,
  normalizedCenterY: 0,
  normalizedCenterZ: 0,
};

const DEFAULT_CT_PARAMS: CtParamsPreviewParams = {
  gantryPitchDeg: 0,
  gantryYawDeg: 0,
  gantryRollDeg: 0,
  sliceThicknessMm: 5.0,
  doseLevel: 'standard',
  mAs: 150,
  kVp: 120,
  pitch: 1.2,
  fovMm: 250,
  matrixSize: 256,
  kernel: 'bone',
  contrastPhase: 'venous',
};

const DEFAULT_MAS = 150;
const MIN_MAS = 30;
const MAX_MAS = 300;

const lesionTypeLabel: Record<LesionType, string> = {
  tumor: 'Tumor',
  nodule: 'Nodule',
  cyst: 'Cyst',
  calcification: 'Calcification',
  metastasis: 'Metastasis',
};

const shapeLabel: Record<LesionShape, string> = {
  spherical: 'Spherical',
  ellipsoidal: 'Ellipsoidal',
  irregular: 'Irregular',
  lobulated: 'Lobulated',
  spiculated: 'Spiculated',
};

/** Distinct colors for 3D lesion overlay (up to 8 lesions) */
const LESION_OVERLAY_COLORS: Array<[number, number, number]> = [
  [255, 60, 60],    // Red
  [60, 200, 80],    // Green
  [60, 140, 255],   // Blue
  [255, 210, 60],   // Gold
  [200, 60, 255],   // Purple
  [255, 120, 40],   // Orange
  [60, 255, 220],   // Teal
  [255, 70, 160],   // Pink
];

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
  const { lesions, organs, addLesion, removeLesion, activeJobs, completedJobs, addJob } =
    useSimulationStore();

  // ---- Tab state ----
  const [activeTab, setActiveTab] = useState<
    'lesion' | 'organ' | 'deformation' | 'phantom'
  >('lesion');

  // ---- Source volume state ----
  const [sourceType, setSourceType] = useState<'synthetic' | 'dicom'>('synthetic');
  const [studies, setStudies] = useState<DicomStudy[]>([]);
  const [seriesList, setSeriesList] = useState<DicomSeries[]>([]);
  const [selectedStudyId, setSelectedStudyId] = useState<string | null>(null);
  const [selectedSeriesId, setSelectedSeriesId] = useState<string | null>(null);
  const [loadingStudies, setLoadingStudies] = useState(false);
  const [loadingSeries, setLoadingSeries] = useState(false);
  const [atlasCases, setAtlasCases] = useState<AtlasCaseOption[]>([]);
  const [loadingAtlasCases, setLoadingAtlasCases] = useState(false);
  const [selectedAtlasCaseId, setSelectedAtlasCaseId] = useState('LUNG1-001');

  // ---- Form state ----
  const [form, setForm] = useState(DEFAULT_FORM);

  // ---- Preview state ----
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewResult, setPreviewResult] = useState<string | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);

  // ---- DICOM preview state ----
  const [dicomPreviewLoading, setDicomPreviewLoading] = useState(false);
  const [dicomPreviewImage, setDicomPreviewImage] = useState<string | null>(null);
  const [dicomPreviewStats, setDicomPreviewStats] = useState<string | null>(null);
  const [dicomPreviewError, setDicomPreviewError] = useState<string | null>(null);

  // ---- Job running state ----
  const [jobLoading, setJobLoading] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);

  // ---- Export state ----
  const [exportFormat, setExportFormat] = useState<'dicom' | 'nifti' | 'nrrd'>('dicom');
  const [exporting, setExporting] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [exportError, setExportError] = useState<string | null>(null);

  const latestCompletedJob = completedJobs[completedJobs.length - 1];

  // ---- CT Phantom state ----
  const [phantom, setPhantom] = useState<PhantomResponse | null>(null);
  const [phantomLoading, setPhantomLoading] = useState(false);
  const [phantomError, setPhantomError] = useState<string | null>(null);
  const [ctWorkspaceSource, setCtWorkspaceSource] = useState<'atlas' | 'procedural' | 'dicom'>('atlas');
  const [phantomSize, setPhantomSize] = useState(192);
  const [loadedPhantomSize, setLoadedPhantomSize] = useState<number | null>(null);
  const [sliceIndex, setSliceIndex] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [playSpeed, setPlaySpeed] = useState(10); // slices per second
  const [sync3DToSlice, setSync3DToSlice] = useState(true);
  const [activePreset, setActivePreset] = useState<WindowPreset>(WINDOW_PRESETS[0]);
  const [showLabelOverlay, setShowLabelOverlay] = useState(true);
  const [pickingMode, setPickingMode] = useState(false);
  const [pickedPosition, setPickedPosition] = useState<{ x: number; y: number; z: number } | null>(null);
  const [pickedWorldPositionMm, setPickedWorldPositionMm] = useState<[number, number, number] | null>(null);
  const [ctParams, setCtParams] = useState<CtParamsPreviewParams>(DEFAULT_CT_PARAMS);
  const [mAsInput, setMAsInput] = useState(String(DEFAULT_CT_PARAMS.mAs));
  const [ctParamsLoading, setCtParamsLoading] = useState(false);
  const [ctParamsError, setCtParamsError] = useState<string | null>(null);
  const [ctParamsResult, setCtParamsResult] = useState<CtParamsPreviewResponse | null>(null);
  const [isCtParamsPanelOpen, setIsCtParamsPanelOpen] = useState(false);
  const [ctParamsCopyState, setCtParamsCopyState] = useState<string | null>(null);
  const [ctParamsDownloadState, setCtParamsDownloadState] = useState<string | null>(null);
  const [standardizedCaseCopyState, setStandardizedCaseCopyState] = useState<string | null>(null);
  const [standardizedCaseDownloadState, setStandardizedCaseDownloadState] = useState<string | null>(null);

  // ---- 3D lesion mesh preview state ----
  const [meshPreviewOpen, setMeshPreviewOpen] = useState(false);
  const [meshPreviewData, setMeshPreviewData] = useState<LesionMeshData | null>(null);
  const [meshPreviewLoading, setMeshPreviewLoading] = useState(false);
  const [meshPreviewError, setMeshPreviewError] = useState<string | null>(null);
  /** Phantom volume data for the in-body 3D preview */
  const [previewPhantomData, setPreviewPhantomData] = useState<Float32Array | null>(null);
  const [previewPhantomDims, setPreviewPhantomDims] = useState<[number, number, number] | null>(null);
  const [previewPhantomSpacing, setPreviewPhantomSpacing] = useState<[number, number, number] | null>(null);

  // ---- Phase 5: Lesion mesh overlay on CT volume state ----
  const [lesionOverlayLoading, setLesionOverlayLoading] = useState(false);
  const [lesionOverlayError, setLesionOverlayError] = useState<string | null>(null);
  /** Base mesh data per lesion index (geometry in CT phantom physical coords) */
  const [lesionOverlayBaseMeshes, setLesionOverlayBaseMeshes] = useState<LesionMeshData[]>([]);
  /** Global opacity for all overlay meshes (0..1) */
  const [lesionOverlayOpacity, setLesionOverlayOpacity] = useState(0.5);
  /** Per-lesion visibility keyed by lesion index */
  const [lesionOverlayVisibleMap, setLesionOverlayVisibleMap] = useState<Record<number, boolean>>({});

  // ---- Active rendering data ----
  const [decodedPhantomData, setDecodedPhantomData] = useState<Float32Array | null>(null);
  const [decodedLabelData, setDecodedLabelData] = useState<Uint8Array | null>(null);
  const [vtkVolumeData, setVtkVolumeData] = useState<Float32Array | null>(null);
  const [vtkDims, setVtkDims] = useState<[number, number, number] | null>(null);
  const [vtkSpacing, setVtkSpacing] = useState<[number, number, number] | null>(null);

  // ---- Refs ----
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const playTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    if (!ctParamsCopyState) return;
    const timer = window.setTimeout(() => setCtParamsCopyState(null), 2000);
    return () => window.clearTimeout(timer);
  }, [ctParamsCopyState]);

  useEffect(() => {
    if (!ctParamsDownloadState) return;
    const timer = window.setTimeout(() => setCtParamsDownloadState(null), 2000);
    return () => window.clearTimeout(timer);
  }, [ctParamsDownloadState]);

  useEffect(() => {
    if (!standardizedCaseCopyState) return;
    const timer = window.setTimeout(() => setStandardizedCaseCopyState(null), 2000);
    return () => window.clearTimeout(timer);
  }, [standardizedCaseCopyState]);

  useEffect(() => {
    if (!standardizedCaseDownloadState) return;
    const timer = window.setTimeout(() => setStandardizedCaseDownloadState(null), 2000);
    return () => window.clearTimeout(timer);
  }, [standardizedCaseDownloadState]);

  // ---- Decode phantom data and labels when phantom changes ----
  useEffect(() => {
    if (!phantom) {
      setLoadedPhantomSize(null);
      setDecodedPhantomData(null);
      setDecodedLabelData(null);
      return;
    }

    try {
      setDecodedPhantomData(decodeBase64ToFloat32(phantom.volumeBase64));
      setDecodedLabelData(
        phantom.labelBase64
        ? decodeBase64ToUint8(phantom.labelBase64)
        : null,
      );
    } catch (err: any) {
      console.error('[SimulationPage] Failed to decode phantom volume:', err);
      setDecodedPhantomData(null);
      setDecodedLabelData(null);
    }
  }, [phantom?.volumeBase64, phantom?.labelBase64]);

  // ---- Generate 3D lesion overlay mask for vtk.js volume preview ----
  const lesionOverlay = useMemo(() => {
    if (!phantom || lesions.length === 0) return null;

    const { width, height, depth } = phantom.metadata;
    const sp = phantom.metadata.spacing; // [sp_z, sp_y, sp_x] from backend
    const totalVoxels = width * height * depth;
    const mask = new Float32Array(totalVoxels);
    // Track closest center distance per voxel (for overlap resolution)
    const closestDist = new Float32Array(totalVoxels);
    closestDist.fill(Infinity);

    const labels: Array<{ index: number; name: string; color: [number, number, number] }> = [];

    for (let li = 0; li < lesions.length && li < LESION_OVERLAY_COLORS.length; li++) {
      const lesion = lesions[li];
      const labelIdx = LESION_LABEL_BASE + li + 1;
      labels.push({
        index: labelIdx,
        name: `Lesion ${li + 1}: ${lesionTypeLabel[lesion.type]}`,
        color: LESION_OVERLAY_COLORS[li],
      });

      // Center in voxel space (x=column, y=row, z=slice)
      const cx = lesion.center[0] || width / 2;
      const cy = lesion.center[1] || height / 2;
      const cz = lesion.center[2] || depth / 2;

      // Radii in voxels: radiusMm / voxel spacing
      const rx = Math.max(1, lesion.radiusMm[0] / sp[2]);
      const ry = Math.max(1, lesion.radiusMm[1] / sp[1]);
      const rz = Math.max(1, lesion.radiusMm[2] / sp[0]);

      // Conservative bounding box (±1.5 × radius for spiculated/lobulated)
      const margin = 1.5;
      const z0 = Math.max(0, Math.floor(cz - rz * margin));
      const z1 = Math.min(depth - 1, Math.ceil(cz + rz * margin));
      const y0 = Math.max(0, Math.floor(cy - ry * margin));
      const y1 = Math.min(height - 1, Math.ceil(cy + ry * margin));
      const x0 = Math.max(0, Math.floor(cx - rx * margin));
      const x1 = Math.min(width - 1, Math.ceil(cx + rx * margin));

      for (let z = z0; z <= z1; z++) {
        const dz = (z - cz) / rz;
        for (let y = y0; y <= y1; y++) {
          const dy = (y - cy) / ry;
          for (let x = x0; x <= x1; x++) {
            const dx = (x - cx) / rx;
            const r2 = dx * dx + dy * dy + dz * dz;

            // Approximate shape perturbation for visual preview
            let dist2 = r2;
            if (lesion.shape === 'lobulated') {
              const r = Math.sqrt(r2);
              if (r > 0.01) {
                const theta = Math.atan2(dy, dx);
                const phi = Math.acos(Math.max(-1, Math.min(1, dz / r)));
                dist2 -= 0.18 * Math.sin(3 * theta) * Math.sin(2 * phi);
              }
            } else if (lesion.shape === 'spiculated') {
              const r = Math.sqrt(r2);
              if (r > 0.01) {
                const theta = Math.atan2(dy, dx);
                const phi = Math.acos(Math.max(-1, Math.min(1, dz / r)));
                dist2 -= 0.28 * Math.abs(Math.sin(6 * theta) * Math.sin(4 * phi));
              }
            } else if (lesion.shape === 'irregular') {
              const hash = Math.sin(dx * 12.9898 + dy * 78.233 + dz * 45.164) * 43758.5453;
              dist2 += (hash - Math.floor(hash)) * 0.18 - 0.09;
            }

            if (dist2 < 1.0) {
              const idx = z * height * width + y * width + x;
              // Resolve overlaps: keep the lesion with the closest center
              if (dist2 < closestDist[idx]) {
                mask[idx] = labelIdx;
                closestDist[idx] = dist2;
              }
            }
          }
        }
      }
    }

    return { mask, labels };
  }, [lesions, phantom]);

  // ---- Phase 5: Lesion mesh overlay (transformed to CT phantom coords) ----
  const lesionOverlayMeshes = useMemo<LesionMeshData[]>(() => {
    if (lesionOverlayBaseMeshes.length === 0) return [];

    return lesionOverlayBaseMeshes.map((base, i) => ({
      ...base,
      opacity: lesionOverlayOpacity,
      visible: lesionOverlayVisibleMap[i] !== false,
    }));
  }, [lesionOverlayBaseMeshes, lesionOverlayOpacity, lesionOverlayVisibleMap]);

  const simulatedVolumeData = useMemo(() => {
    if (!ctParamsResult?.simulatedVolumeBase64) return null;
    try {
      return decodeBase64ToFloat32(ctParamsResult.simulatedVolumeBase64);
    } catch (err) {
      console.error('[SimulationPage] Failed to decode simulated CT volume:', err);
      return null;
    }
  }, [ctParamsResult?.simulatedVolumeBase64]);
  const simulatedLabelData = useMemo(() => {
    if (!ctParamsResult?.simulatedLabelBase64) return null;
    try {
      return decodeBase64ToUint8(ctParamsResult.simulatedLabelBase64);
    } catch (err) {
      console.error('[SimulationPage] Failed to decode simulated label volume:', err);
      return null;
    }
  }, [ctParamsResult?.simulatedLabelBase64]);

  const activeVolumeShape = useMemo<[number, number, number] | null>(() => {
    if (ctParamsResult?.metadata.shape && ctParamsResult.metadata.shape.length >= 3) {
      const [depth, height, width] = ctParamsResult.metadata.shape;
      return [depth, height, width];
    }

    if (!phantom) return null;
    return [phantom.metadata.depth, phantom.metadata.height, phantom.metadata.width];
  }, [ctParamsResult?.metadata.shape, phantom]);

  const activeVolumeSpacing = useMemo<[number, number, number] | null>(() => {
    if (ctParamsResult?.metadata.spacing && ctParamsResult.metadata.spacing.length >= 3) {
      const [z, y, x] = ctParamsResult.metadata.spacing;
      return [z, y, x];
    }

    if (!phantom) return null;
    const [z, y, x] = phantom.metadata.spacing;
    return [z, y, x];
  }, [ctParamsResult?.metadata.spacing, phantom]);

  const activeSliceData = ctParamsResult ? simulatedVolumeData : decodedPhantomData;
  const activeLabelData = ctParamsResult ? simulatedLabelData : decodedLabelData;
  const activeBodySliceRange = useMemo(() => {
    if (!activeSliceData || !activeVolumeShape) return null;
    const threshold = phantom?.metadata.bodyThresholdHU ?? -500;
    return findInformativeSliceRange(activeSliceData, activeVolumeShape, threshold);
  }, [activeSliceData, activeVolumeShape, phantom?.metadata.bodyThresholdHU]);
  const activePickedPosition = useMemo(() => {
    if (!pickedWorldPositionMm || !activeVolumeShape || !activeVolumeSpacing) {
      return pickedPosition;
    }

    const [x, y, z] = centeredWorldMmToVoxel(
      pickedWorldPositionMm,
      activeVolumeShape,
      activeVolumeSpacing,
    );
    const clampedX = Math.max(0, Math.min(Math.round(x), activeVolumeShape[2] - 1));
    const clampedY = Math.max(0, Math.min(Math.round(y), activeVolumeShape[1] - 1));
    const clampedZ = Math.max(0, Math.min(Math.round(z), activeVolumeShape[0] - 1));

    return { x: clampedX, y: clampedY, z: clampedZ };
  }, [activeVolumeShape, activeVolumeSpacing, pickedPosition, pickedWorldPositionMm]);
  const activeSegmentationLabelData = useMemo(() => {
    if (!activeLabelData || !activeVolumeShape) return null;

    const expectedVoxelCount = activeVolumeShape[0] * activeVolumeShape[1] * activeVolumeShape[2];
    if (activeLabelData.length !== expectedVoxelCount) return null;

    return activeLabelData;
  }, [activeLabelData, activeVolumeShape]);
  const totalSlices = activeVolumeShape?.[0] ?? 0;
  const scanStartIndex = activeBodySliceRange?.startIndex ?? 0;
  const scanEndIndex = activeBodySliceRange?.endIndex ?? Math.max(totalSlices - 1, 0);
  const activePreviewSource = ctParamsResult?.metadata.source ?? phantom?.metadata.source ?? 'atlas';
  const activeVolumeSourceLabel = ctParamsResult
    ? 'Simulated CT'
    : activePreviewSource === 'dicom'
      ? 'DICOM CT'
      : activePreviewSource === 'procedural'
        ? 'Procedural CT'
        : 'Atlas CT';
  const activeSpacingLabel = activeVolumeSpacing
    ? activeVolumeSpacing.map((value) => value.toFixed(2)).join(' / ')
    : null;
  const previewStats = ctParamsResult?.metadata.previewStats;
  const originalCenterSliceStats = previewStats?.originalCenterSliceStats;
  const simulatedCenterSliceStats = previewStats?.simulatedCenterSliceStats;
  const ctWorkspaceSeriesList = useMemo(
    () => seriesList.filter((series) => !series.modality || series.modality.toUpperCase() === 'CT'),
    [seriesList],
  );
  const loadedWorkspaceStudyId = phantom?.metadata.studyId ?? null;
  const loadedWorkspaceSeriesId = phantom?.metadata.seriesId ?? null;
  const activeVolumeDatasetKey = ctParamsResult?.simulatedVolumeBase64 ?? phantom?.volumeBase64 ?? null;

  const organSegmentationOverlay = useMemo(() => {
    if (!showLabelOverlay || !phantom?.metadata?.labelMap || !activeSegmentationLabelData || !activeVolumeShape) {
      return null;
    }

    const availableLabels = Object.entries(phantom.metadata.labelMap)
      .map(([key, value]) => [Number(key), value] as const)
      .filter(([index]) => index > 0 && !!ORGAN_COLORS[index]);

    if (availableLabels.length === 0) return null;

    const orderedLabels = ORGAN_LABEL_PRIORITY
      .filter((index) => availableLabels.some(([availableIndex]) => availableIndex === index))
      .map((index) => {
        const rawName = availableLabels.find(([availableIndex]) => availableIndex === index)?.[1] ?? `Label ${index}`;
        return {
          index,
          name: formatOrganLabelName(rawName),
          color: ORGAN_COLORS[index],
        };
      });

    return {
      // Keep the original CT body rendering intact and overlay label colors
      // as a lightweight translucent volume instead of a separate 3D shell.
      mask: Float32Array.from(activeSegmentationLabelData),
      labels: orderedLabels,
    };
  }, [activeSegmentationLabelData, activeVolumeShape, phantom?.metadata?.labelMap, showLabelOverlay]);

  const compositeSegmentationOverlay = useMemo(() => {
    const organMask = organSegmentationOverlay?.mask ?? null;
    const organLabels = organSegmentationOverlay?.labels ?? [];
    const lesionMask = lesionOverlay?.mask ?? null;
    const lesionLabels = lesionOverlay?.labels ?? [];

    if (!organMask && !lesionMask) return null;
    if (!organMask) return lesionOverlay;
    if (!lesionMask) return organSegmentationOverlay;
    if (organMask.length !== lesionMask.length) return organSegmentationOverlay;

    const combinedMask = new Float32Array(organMask);
    for (let i = 0; i < lesionMask.length; i++) {
      if (lesionMask[i] > 0) {
        combinedMask[i] = lesionMask[i];
      }
    }

    return {
      mask: combinedMask,
      labels: [...organLabels, ...lesionLabels],
    };
  }, [lesionOverlay, organSegmentationOverlay]);
  const visibleLabelLegendItems = useMemo(() => {
    const labelMap = phantom?.metadata?.labelMap;
    if (!labelMap) return [];

    const nonzeroCounts = phantom.metadata.labelNonzeroCounts ?? {};
    const available = Object.entries(labelMap)
      .map(([key, value]) => [Number(key), value] as const)
      .filter(([index]) => index > 0 && !!ORGAN_COLORS[index])
      .filter(([index]) => {
        const count = nonzeroCounts[index];
        return count === undefined || Number(count) > 0;
      });

    const orderedIndexes = [
      ...ORGAN_LABEL_PRIORITY.filter((index) => available.some(([availableIndex]) => availableIndex === index)),
      ...available
        .map(([index]) => index)
        .filter((index) => !ORGAN_LABEL_PRIORITY.includes(index)),
    ];

    return orderedIndexes.map((index) => {
      const rawName = available.find(([availableIndex]) => availableIndex === index)?.[1] ?? `Label ${index}`;
      return {
        index,
        name: formatOrganLabelName(rawName),
        color: ORGAN_COLORS[index],
      };
    });
  }, [phantom?.metadata?.labelMap, phantom?.metadata?.labelNonzeroCounts]);

  useEffect(() => {
    if (!activeVolumeShape) return;
    const maxIndex = Math.max(activeVolumeShape[0] - 1, 0);
    if (sliceIndex > maxIndex) {
      setSliceIndex(maxIndex);
    }
  }, [activeVolumeShape, sliceIndex]);

  useEffect(() => {
    if (!activeVolumeDatasetKey) return;
    setSliceIndex(scanStartIndex);
    setPlaying(false);
  }, [activeVolumeDatasetKey, scanStartIndex]);

  useEffect(() => {
    if (!activeVolumeShape || !activeVolumeSpacing || !activeSliceData) {
      setVtkVolumeData(null);
      setVtkDims(null);
      setVtkSpacing(null);
      return;
    }

    try {
      const { transposedData, dims, vtkSpacing: spacing } = buildVtkVolumePayload(
        activeSliceData,
        activeVolumeShape,
        activeVolumeSpacing,
      );
      setVtkVolumeData(transposedData);
      setVtkDims(dims);
      setVtkSpacing(spacing);
    } catch (err) {
      console.error('[SimulationPage] Failed to prepare VTK volume payload:', err);
      setVtkVolumeData(null);
      setVtkDims(null);
      setVtkSpacing(null);
    }
  }, [activeSliceData, activeVolumeShape, activeVolumeSpacing]);

  // ---- Axial slice canvas rendering ----
  const renderSlice = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas || !activeVolumeShape || !activeSliceData) return;

    const [depth, height, width] = activeVolumeShape;
    const { windowLevel, windowWidth } = activePreset;
    renderVolumeSliceToCanvas({
      canvas,
      width,
      height,
      depth,
      volumeData: activeSliceData,
      sliceIndex,
      windowLevel,
      windowWidth,
      labelData: activeLabelData,
      showLabelOverlay,
      pickedPosition: activePickedPosition,
    });
  }, [
    activePreset,
    activeSliceData,
    activeLabelData,
    activePickedPosition,
    activeVolumeShape,
    showLabelOverlay,
    sliceIndex,
  ]);

  // Re-render on slice/preset change
  useEffect(() => {
    renderSlice();
  }, [renderSlice]);

  // ---- Playback effect ----
  useEffect(() => {
    if (!playing || totalSlices <= 0) return;

    const intervalMs = 1000 / Math.max(playSpeed, 1);
    playTimerRef.current = setInterval(() => {
      setSliceIndex((prev) => {
        const maxIdx = scanEndIndex;
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
  }, [playing, playSpeed, scanEndIndex, totalSlices]);

  const refreshDicomStudies = useCallback(async () => {
    setLoadingStudies(true);
    try {
      const res = await dicomService.getStudies(1, 50);
      setStudies(res.items ?? []);
    } catch {
      // Silently ignore 鈥?studies fetch is non-critical
    } finally {
      setLoadingStudies(false);
    }
  }, []);

  const refreshAtlasCases = useCallback(async () => {
    setLoadingAtlasCases(true);
    try {
      const res = await simulationService.getAtlasCases();
      const nextItems = res.items ?? [];
      setAtlasCases(nextItems);
      if (nextItems.length > 0) {
        setSelectedAtlasCaseId((prev) =>
          nextItems.some((item) => item.caseId === prev) ? prev : nextItems[0].caseId,
        );
      }
    } catch {
      // Silently ignore 鈥?atlas listing is non-critical
    } finally {
      setLoadingAtlasCases(false);
    }
  }, []);

  // ---- Load available DICOM studies on mount ----
  useEffect(() => {
    let mounted = true;
    const loadStudies = async () => {
      setLoadingStudies(true);
      try {
        const res = await dicomService.getStudies(1, 50);
        if (mounted) setStudies(res.items ?? []);
      } catch {
        // Silently ignore — studies fetch is non-critical
      } finally {
        if (mounted) setLoadingStudies(false);
      }
    };
    loadStudies();
    return () => { mounted = false; };
  }, []);

  useEffect(() => {
    void refreshAtlasCases();
  }, [refreshAtlasCases]);

  // ---- Load series when a study is selected ----
  useEffect(() => {
    if (!selectedStudyId) {
      setSeriesList([]);
      setSelectedSeriesId(null);
      return;
    }
    let mounted = true;
    const loadSeries = async () => {
      setLoadingSeries(true);
      setSelectedSeriesId(null);
      try {
        const series = await dicomService.getSeries(selectedStudyId);
        // Filter to CT/MR imaging modalities only
        const IMAGE_MODALITIES = ['CT', 'MR', 'PT', 'NM', 'US', 'XA', 'CR'];
        if (mounted) setSeriesList(series.filter((s) => IMAGE_MODALITIES.includes(s.modality)));
      } catch {
        // Silently ignore
      } finally {
        if (mounted) setLoadingSeries(false);
      }
    };
    loadSeries();
    return () => { mounted = false; };
  }, [selectedStudyId]);

  // -----------------------------------------------------------------------
  // Handlers: phantom
  // -----------------------------------------------------------------------

  const handleGeneratePhantom = async () => {
    if (ctWorkspaceSource === 'dicom' && !selectedSeriesId) {
      setPhantomError('Select a DICOM CT series before loading the CT workspace.');
      return;
    }

    setPickedPosition(null);
    setPickedWorldPositionMm(null);
    setPickingMode(false);
    setPhantomLoading(true);
    setPhantomError(null);
    setCtParamsError(null);
    setCtParamsResult(null);
    setPhantom(null);
    setSliceIndex(0);
    setPlaying(false);
    try {
      const size = phantomSize;
      const response = await simulationService.getPhantom(
        ctWorkspaceSource,
        size,
        selectedAtlasCaseId,
        'head_to_feet',
        ctWorkspaceSource === 'dicom' ? selectedStudyId : null,
        ctWorkspaceSource === 'dicom' ? selectedSeriesId : null,
      );
      setPhantom(response);
      setLoadedPhantomSize(size);
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
    if (totalSlices <= 0) return;
    if (playing) {
      setPlaying(false);
    } else {
      // If at end, restart from top
      if (sliceIndex >= scanEndIndex) {
        setSliceIndex(scanStartIndex);
      }
      setPlaying(true);
    }
  };

  const handleSliceChange = (value: number) => {
    setSliceIndex(value);
  };

  const normalizeMAsInput = useCallback(() => {
    const trimmed = mAsInput.trim();
    const parsed = Number(trimmed);
    const normalized = !trimmed || Number.isNaN(parsed)
      ? DEFAULT_MAS
      : Math.max(MIN_MAS, Math.min(MAX_MAS, Math.round(parsed)));

    setCtParams((prev) => ({ ...prev, mAs: normalized }));
    setMAsInput(String(normalized));
    return normalized;
  }, [mAsInput]);

  const handleResetCtAngles = useCallback(() => {
    setCtParams((prev) => ({
      ...prev,
      gantryPitchDeg: 0,
      gantryYawDeg: 0,
      gantryRollDeg: 0,
    }));
  }, []);

  const handleRunCtParamsSimulation = async (
    overrideParams?: Partial<CtParamsPreviewParams>,
  ) => {
    if (!phantom) {
      setCtParamsError('Please generate a CT phantom first.');
      return;
    }

    const currentSource = phantom.metadata.source ?? 'atlas';
    if (currentSource !== 'atlas' && currentSource !== 'procedural' && currentSource !== 'dicom') {
      setCtParamsError(`Unsupported source for CT parameter simulation: ${String(currentSource)}`);
      return;
    }

    const normalizedMAs = normalizeMAsInput();
    const normalizedParams: CtParamsPreviewParams = {
      ...ctParams,
      ...overrideParams,
      mAs: normalizedMAs,
    };

    setCtParamsLoading(true);
    setCtParamsError(null);
    setCtParamsCopyState(null);
    setCtParamsDownloadState(null);
    setStandardizedCaseCopyState(null);
    setStandardizedCaseDownloadState(null);
    try {
      const response = await simulationService.runCtParamsPreview({
        source: currentSource,
        caseId: currentSource === 'atlas' ? (phantom.metadata.caseId || 'LUNG1-001') : null,
        studyId: currentSource === 'dicom' ? loadedWorkspaceStudyId : null,
        seriesId: currentSource === 'dicom' ? loadedWorkspaceSeriesId : null,
        size: loadedPhantomSize ?? Math.max(phantom.metadata.depth, phantom.metadata.height, phantom.metadata.width),
        scanDirection: 'head_to_feet',
        params: normalizedParams,
      });
      setCtParamsResult(response);
      setPlaying(false);
    } catch (err: any) {
      console.error('[SimulationPage] CT parameter simulation failed:', err);
      setCtParamsError(err?.message || 'Failed to run CT parameter simulation.');
    } finally {
      setCtParamsLoading(false);
    }
  };

  const handleCopyCtParamsJson = async () => {
    if (!ctParamsResult) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(ctParamsResult.paramsJson, null, 2));
      setCtParamsCopyState('Copied');
    } catch {
      setCtParamsCopyState('Copy failed');
    }
  };

  const handleDownloadCtParamsJson = useCallback(() => {
    if (!ctParamsResult) return;
    try {
      const caseId = phantom?.metadata.caseId || 'LUNG1-001';
      const filename = `ct_params_simulation_${caseId}_${formatTimestampForFilename(new Date())}.json`;
      const blob = new Blob([JSON.stringify(ctParamsResult.paramsJson, null, 2)], {
        type: 'application/json;charset=utf-8',
      });
      triggerDownload(blob, filename);
      setCtParamsDownloadState('Downloaded');
    } catch (err) {
      console.error('[SimulationPage] CT params JSON download failed:', err);
      setCtParamsDownloadState('Download failed');
    }
  }, [ctParamsResult, phantom?.metadata.caseId]);

  const handleCopyStandardizedCaseJson = async () => {
    if (!ctParamsResult?.standardizedCase) return;
    try {
      await navigator.clipboard.writeText(JSON.stringify(ctParamsResult.standardizedCase, null, 2));
      setStandardizedCaseCopyState('Copied');
    } catch {
      setStandardizedCaseCopyState('Copy failed');
    }
  };

  const handleDownloadStandardizedCaseJson = useCallback(() => {
    if (!ctParamsResult?.standardizedCase) return;
    try {
      const caseId = ctParamsResult.standardizedCase.caseId;
      const filename = `standardized_ct_case_${caseId}.json`;
      const blob = new Blob([JSON.stringify(ctParamsResult.standardizedCase, null, 2)], {
        type: 'application/json;charset=utf-8',
      });
      triggerDownload(blob, filename);
      setStandardizedCaseDownloadState('Downloaded');
    } catch (err) {
      console.error('[SimulationPage] Standardized case JSON download failed:', err);
      setStandardizedCaseDownloadState('Download failed');
    }
  }, [ctParamsResult]);

  // -----------------------------------------------------------------------
  // Handlers: lesion form
  // -----------------------------------------------------------------------

  /** Update a single form field */
  const updateForm = useCallback(
    <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
      setForm((prev) => ({ ...prev, [key]: value })),
    [],
  );

  /** Handle click on CT phantom canvas to pick lesion position */
  const handleCanvasClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!pickingMode || !activeVolumeShape) return;

      const canvas = canvasRef.current;
      if (!canvas) return;

      const rect = canvas.getBoundingClientRect();
      const scaleX = canvas.width / rect.width;
      const scaleY = canvas.height / rect.height;

      const x = Math.round((e.clientX - rect.left) * scaleX);
      const y = Math.round((e.clientY - rect.top) * scaleY);

      // Clamp to volume bounds
      const [depth, height, width] = activeVolumeShape;
      const clampedX = Math.max(0, Math.min(x, width - 1));
      const clampedY = Math.max(0, Math.min(y, height - 1));
      const clampedZ = Math.max(0, Math.min(sliceIndex, depth - 1));

      // Store absolute phantom coords (for crosshair display)
      setPickedPosition({ x: clampedX, y: clampedY, z: clampedZ });
      setPickedWorldPositionMm(
        voxelToCenteredWorldMm(
          [clampedX, clampedY, clampedZ],
          activeVolumeShape,
          activeVolumeSpacing ?? [1, 1, 1],
        ),
      );

      // Store absolute phantom coords (for display in number inputs, works for same-volume synthetic)
      updateForm('centerX', clampedX);
      updateForm('centerY', clampedY);
      updateForm('centerZ', clampedZ);

      // Store normalized coords (0-1) for cross-volume scaling (e.g., phantom → DICOM)
      const normX = width > 1 ? clampedX / (width - 1) : 0;
      const normY = height > 1 ? clampedY / (height - 1) : 0;
      const normZ = depth > 1 ? clampedZ / (depth - 1) : 0;
      updateForm('normalizedCenterX', normX);
      updateForm('normalizedCenterY', normY);
      updateForm('normalizedCenterZ', normZ);
      setSourceType(activePreviewSource === 'dicom' ? 'dicom' : 'synthetic');
      setPickingMode(false);
      setActiveTab('lesion');
    },
    [activePreviewSource, activeVolumeShape, activeVolumeSpacing, pickingMode, sliceIndex, updateForm],
  );

  /** Add a lesion from the current form values */
  const handleAddLesion = useCallback(() => {
    const radius = form.diameter / 2;

    // Compute center coords based on target volume
    let cx = form.centerX;
    let cy = form.centerY;
    let cz = form.centerZ;

    // When normalized coords are set (from phantom pick) and target is DICOM,
    // scale to DICOM volume dimensions.
    // Skip if user manually cleared center values (wanting auto-center: 0→backend).
    if (
      sourceType === 'dicom' &&
      form.normalizedCenterX > 0 &&
      selectedSeriesId &&
      form.centerX !== 0 && form.centerY !== 0 && form.centerZ !== 0
    ) {
      const series = seriesList.find((s) => s.id === selectedSeriesId);
      if (series) {
        cx = form.normalizedCenterX * Math.max(series.columns - 1, 0);
        cy = form.normalizedCenterY * Math.max(series.rows - 1, 0);
        cz = form.normalizedCenterZ * Math.max(series.imageCount - 1, 0);
      }
    }

    const lesion: LesionConfig = {
      type: form.lesionType,
      shape: form.shape,
      center: [cx, cy, cz],
      radiusMm: [radius, radius, radius],
      huMean: form.huMean,
      huStd: form.huStd,
      marginSharpness: form.marginSharpness,
      calcificationFraction: 0,
      necrosisFraction: 0,
      spiculationDegree: form.spiculationDegree,
    };
    addLesion(lesion);
  }, [form, addLesion, sourceType, selectedSeriesId, seriesList]);

  /** Run simulation with all configured lesions */
  const handleRunSimulation = useCallback(async () => {
    if (lesions.length === 0 && organs.length === 0) {
      setJobError('Add at least one lesion or organ before running simulation');
      return;
    }

    if (sourceType === 'dicom' && !selectedSeriesId) {
      setJobError('Select a DICOM series to use as the base volume');
      return;
    }

    setJobLoading(true);
    setJobError(null);

    try {
      const jobConfig: Record<string, unknown> = {
        lesions,
        organs,
        outputFormat: 'dicom',
      };
      if (sourceType === 'dicom' && selectedStudyId && selectedSeriesId) {
        jobConfig.studyId = selectedStudyId;
        jobConfig.seriesId = selectedSeriesId;
      }
      const job = await simulationService.createJob(jobConfig);
      addJob(job);
    } catch (err: any) {
      setJobError(err?.message || 'Failed to create simulation job');
    } finally {
      setJobLoading(false);
    }
  }, [lesions, organs, addJob, sourceType, selectedStudyId, selectedSeriesId]);

  /** Preview current lesion configuration */
  const handlePreview = useCallback(async () => {
    const radius = form.diameter / 2;
    const lesion: LesionConfig = {
      type: form.lesionType,
      shape: form.shape,
      center: [form.centerX, form.centerY, form.centerZ],
      radiusMm: [radius, radius, radius],
      huMean: form.huMean,
      huStd: form.huStd,
      marginSharpness: form.marginSharpness,
      calcificationFraction: 0,
      necrosisFraction: 0,
      spiculationDegree: form.spiculationDegree,
    };

    setPreviewLoading(true);
    setPreviewResult(null);
    setPreviewError(null);

    try {
      const result = await simulationService.previewLesion(lesion);
      const pd = result.previewData;
      const meanHu = typeof pd.huMean === 'number' ? pd.huMean.toFixed(0) : '?';
      const volMm3 = typeof pd.volumeMm3 === 'number' ? pd.volumeMm3.toFixed(0) : '?';
      setPreviewResult(
        `HU range: [${result.huRange[0].toFixed(0)}, ${result.huRange[1].toFixed(0)}] · ` +
        `Mean: ${meanHu} · Volume: ${volMm3} mm³ · Voxels: ${result.voxelCount}`,
      );
    } catch (err: any) {
      setPreviewError(err?.message || 'Preview failed');
    } finally {
      setPreviewLoading(false);
    }
  }, [form]);

  /**
   * Preview current lesion inside a CT phantom body or DICOM volume in 3D.
   *
   * - If source is 'dicom': calls endpoint that loads the DICOM volume,
   *   downsamples it, bakes the lesion in, and returns volume + mesh.
   * - If source is 'synthetic': calls endpoint that generates a procedural
   *   CT phantom with the lesion embedded.
   *
   * The VolumeRenderer renders the CT volume (mode='synthetic') with
   * the lesion mesh overlaid.
   */
  const handlePreview3D = useCallback(async () => {
    const radius = form.diameter / 2;

    // Reset state
    setMeshPreviewData(null);
    setPreviewPhantomData(null);
    setPreviewPhantomDims(null);
    setPreviewPhantomSpacing(null);
    setMeshPreviewError(null);
    setMeshPreviewLoading(true);
    setMeshPreviewOpen(true);

    try {
      if (activeVolumeShape && activeVolumeSpacing && vtkVolumeData && vtkDims) {
        const mesh: Lesion3DPreviewResponse = await simulationService.previewLesion3D({
          lesionType: form.lesionType,
          shape: form.shape,
          centerX: 0,
          centerY: 0,
          centerZ: 0,
          radiusX: radius,
          radiusY: radius,
          radiusZ: radius,
          huMean: form.huMean,
          huStd: form.huStd,
          marginSharpness: form.marginSharpness,
          spiculationDegree: form.spiculationDegree,
          previewSize: 64,
          spacing: [
            activeVolumeSpacing[0],
            activeVolumeSpacing[1],
            activeVolumeSpacing[2],
          ],
        });

        const [depth, height, width] = activeVolumeShape;
        const cx = form.centerX > 0 ? Math.min(form.centerX, width - 1) : width / 2;
        const cy = form.centerY > 0 ? Math.min(form.centerY, height - 1) : height / 2;
        const cz = form.centerZ > 0 ? Math.min(form.centerZ, depth - 1) : depth / 2;
        const [targetX, targetY, targetZ] = voxelToCenteredWorldMm(
          [cx, cy, cz],
          activeVolumeShape,
          activeVolumeSpacing,
        );

        const tx = targetX - mesh.center[0];
        const ty = targetY - mesh.center[1];
        const tz = targetZ - mesh.center[2];
        const translatedVertices = mesh.vertices.map(
          (v) => [v[0] + tx, v[1] + ty, v[2] + tz] as [number, number, number],
        );

        setPreviewPhantomData(vtkVolumeData);
        setPreviewPhantomDims(vtkDims);
        setPreviewPhantomSpacing(vtkSpacing ?? [activeVolumeSpacing[2], activeVolumeSpacing[1], activeVolumeSpacing[0]]);
        setMeshPreviewData({
          id: 'preview',
          vertices: translatedVertices,
          faces: mesh.faces,
          normals: mesh.normals,
          opacity: 1.0,
          color: [1, 0.35, 0.35],
          visible: true,
        });
        return;
      }

      // Shared lesion params
      const lesionParams = {
        lesionType: form.lesionType,
        shape: form.shape,
        normalizedCenterX: form.normalizedCenterX || 0,
        normalizedCenterY: form.normalizedCenterY || 0,
        normalizedCenterZ: form.normalizedCenterZ || 0,
        radiusX: radius,
        radiusY: radius,
        radiusZ: radius,
        huMean: form.huMean,
        huStd: form.huStd,
        marginSharpness: form.marginSharpness,
        spiculationDegree: form.spiculationDegree,
      };

      let volumeBase64: string;
      let volumeShape: [number, number, number];
      let volumeSpacing: [number, number, number];
      let lesionVertices: number[][];
      let lesionFaces: number[][];
      let lesionNormals: number[][];

      if (sourceType === 'dicom' && selectedSeriesId) {
        // ── Branch A: Lesion embedded in real DICOM volume ──
        const result: DicomLesion3DPreviewResponse =
          await simulationService.previewLesionOnDicom3D({
            seriesId: selectedSeriesId,
            scanDirection: 'head_to_feet',
            ...lesionParams,
            previewSize: 192,
          });
        volumeBase64 = result.volumeBase64;
        volumeShape = result.volumeShape;
        volumeSpacing = result.volumeSpacing;
        lesionVertices = result.lesionVertices;
        lesionFaces = result.lesionFaces;
        lesionNormals = result.lesionNormals;

        console.log('[SimulationPage] DICOM 3D preview received', {
          shape: volumeShape,
          spacing: volumeSpacing,
          vertices: lesionVertices?.length,
          faces: lesionFaces?.length,
        });
      } else {
        // ── Branch B: Lesion embedded in procedural CT phantom ──
        const result: LesionInPhantomPreviewResponse =
          await simulationService.previewLesionInPhantom({
            ...lesionParams,
            phantomSize: 160,
          });
        volumeBase64 = result.phantomVolumeBase64;
        volumeShape = result.phantomShape;
        volumeSpacing = result.phantomSpacing;
        lesionVertices = result.lesionVertices;
        lesionFaces = result.lesionFaces;
        lesionNormals = result.lesionNormals;

        console.log('[SimulationPage] phantom 3D preview received', {
          shape: volumeShape,
          spacing: volumeSpacing,
          vertices: lesionVertices?.length,
          faces: lesionFaces?.length,
        });
      }

      // ── Decode volume (base64 → Float32Array) ──
      const binaryStr = atob(volumeBase64);
      const bytes = new Uint8Array(binaryStr.length);
      for (let i = 0; i < binaryStr.length; i++) {
        bytes[i] = binaryStr.charCodeAt(i);
      }
      const floatArray = new Float32Array(bytes.buffer);

      // Backend returns shape/spacing in [z, y, x]; VolumeRenderer expects [x, y, z]
      const [sz, sy, sx] = volumeShape;
      const [spz, spy, spx] = volumeSpacing;
      const dims: [number, number, number] = [sx, sy, sz];
      const spacing: [number, number, number] = [spx, spy, spz];

      setPreviewPhantomData(floatArray);
      setPreviewPhantomDims(dims);
      setPreviewPhantomSpacing(spacing);

      // ── Set lesion mesh (vertices are already in VTK centered space) ──
      setMeshPreviewData({
        id: 'preview',
        vertices: lesionVertices,
        faces: lesionFaces,
        normals: lesionNormals,
        opacity: 1.0,
        color: [1, 0.35, 0.35],
        visible: true,
      });
    } catch (err: any) {
      console.error('[SimulationPage] 3D in-body preview failed:', err);
      setMeshPreviewError(err?.message || '3D preview failed');
    } finally {
      setMeshPreviewLoading(false);
    }
  }, [
    activeVolumeShape,
    activeVolumeSpacing,
    form,
    sourceType,
    selectedSeriesId,
    vtkDims,
    vtkSpacing,
    vtkVolumeData,
  ]);

  /** Remove a lesion by index */
  const handleRemoveLesion = useCallback(
    (index: number) => removeLesion(index),
    [removeLesion],
  );

  /** Preview current lesion on the selected DICOM series */
  const handleDicomPreview = useCallback(async () => {
    if (!selectedSeriesId) {
      setDicomPreviewError('No DICOM series selected');
      return;
    }

    // Find the selected series to get DICOM volume dimensions
    const selectedSeries = seriesList.find((s) => s.id === selectedSeriesId);

    // Compute center: if normalized coords are available (from phantom pick),
    // scale them to the DICOM volume dimensions. Otherwise use centerX/Y/Z
    // as-is (manual entry, or 0 → auto-center in backend).
    let centerX = form.centerX;
    let centerY = form.centerY;
    let centerZ = form.centerZ;

    if (
      selectedSeries &&
      form.normalizedCenterX > 0 &&
      form.normalizedCenterY > 0 &&
      form.normalizedCenterZ > 0 &&
      form.centerX !== 0 && form.centerY !== 0 && form.centerZ !== 0
    ) {
      // Scale normalized phantom coords to DICOM volume space
      centerX = form.normalizedCenterX * selectedSeries.columns;
      centerY = form.normalizedCenterY * selectedSeries.rows;
      centerZ = form.normalizedCenterZ * selectedSeries.imageCount;
    }

    const radius = form.diameter / 2;
    const lesion: LesionConfig = {
      type: form.lesionType,
      shape: form.shape,
      center: [centerX, centerY, centerZ],
      radiusMm: [radius, radius, radius],
      huMean: form.huMean,
      huStd: form.huStd,
      marginSharpness: form.marginSharpness,
      calcificationFraction: 0,
      necrosisFraction: 0,
      spiculationDegree: form.spiculationDegree,
    };

    setDicomPreviewLoading(true);
    setDicomPreviewImage(null);
    setDicomPreviewStats(null);
    setDicomPreviewError(null);

    try {
      const res = await simulationService.previewLesionOnDicom(
        selectedSeriesId,
        lesion,
        40,
        400,
        'head_to_feet',
      );
      // Combine stats into a summary string
      const stats = `HU: ${res.huMean.toFixed(0)} ± ${res.huStd.toFixed(0)} · Range: [${res.huMin.toFixed(0)}, ${res.huMax.toFixed(0)}] · Volume: ${res.volumeMm3.toFixed(0)} mm³ · Slice ${res.sliceIndex + 1}/${res.totalSlices}`;
      setDicomPreviewImage(`data:image/png;base64,${res.imageBase64}`);
      setDicomPreviewStats(stats);
    } catch (err: any) {
      setDicomPreviewError(err?.message || 'DICOM preview failed');
    } finally {
      setDicomPreviewLoading(false);
    }
  }, [form, selectedSeriesId, seriesList]);

  /** Close the DICOM preview modal */
  const closeDicomPreview = useCallback(() => {
    setDicomPreviewImage(null);
    setDicomPreviewStats(null);
    setDicomPreviewError(null);
  }, []);

  /** Close the 3D mesh preview modal */
  const closeMeshPreview = useCallback(() => {
    setMeshPreviewOpen(false);
    setMeshPreviewData(null);
    setMeshPreviewError(null);
    setPreviewPhantomData(null);
    setPreviewPhantomDims(null);
    setPreviewPhantomSpacing(null);
  }, []);

  // -----------------------------------------------------------------------
  // Phase 5: Lesion mesh overlay on CT volume
  // -----------------------------------------------------------------------

  /**
   * Translate a backend 3D mesh (centered in its own preview volume)
   * into the CT phantom's physical coordinate space.
   *
   * The backend generates lesions centered at the preview volume origin.
   * We offset all vertices so the lesion center aligns with its intended
   * position in the CT phantom's physical coordinate system.
   */
  const translateMeshToPhantom = useCallback(
    (
      mesh: Lesion3DPreviewResponse,
      lesionIndex: number,
    ): LesionMeshData | null => {
      if (!phantom || !activeVolumeShape) return null;

      const sp = activeVolumeSpacing ?? phantom.metadata.spacing; // [sp_z, sp_y, sp_x]
      const [depth, height, width] = activeVolumeShape;
      const lesion = lesions[lesionIndex];
      if (!lesion) return null;

      // Lesion center in CT phantom voxel coords (x=column, y=row, z=slice)
      const lx = lesion.center[0];
      const ly = lesion.center[1];
      const lz = lesion.center[2];

      // Clamp to volume bounds (use center if 0 → auto-center later)
      const clampedX = lx > 0 ? Math.min(lx, width - 1) : width / 2;
      const clampedY = ly > 0 ? Math.min(ly, height - 1) : height / 2;
      const clampedZ = lz > 0 ? Math.min(lz, depth - 1) : depth / 2;

      // Target physical position in CT phantom (x, y, z) mm
      const [targetX, targetY, targetZ] = voxelToCenteredWorldMm(
        [clampedX, clampedY, clampedZ],
        activeVolumeShape,
        sp,
      );

      // Mesh center in its own preview volume physical space
      const meshCx = mesh.center[0];
      const meshCy = mesh.center[1];
      const meshCz = mesh.center[2];

      // Translation: bring mesh center to CT phantom target position
      const tx = targetX - meshCx;
      const ty = targetY - meshCy;
      const tz = targetZ - meshCz;

      const translatedVertices = mesh.vertices.map(
        (v) => [v[0] + tx, v[1] + ty, v[2] + tz] as [number, number, number],
      );

      return {
        id: `lesion-${lesionIndex}`,
        vertices: translatedVertices,
        faces: mesh.faces,
        normals: mesh.normals,
        opacity: lesionOverlayOpacity,
        color: LESION_OVERLAY_COLORS[lesionIndex % LESION_OVERLAY_COLORS.length].map(
          (c) => c / 255,
        ) as [number, number, number],
        visible: lesionOverlayVisibleMap[lesionIndex] !== false,
      };
    },
    [phantom, activeVolumeShape, activeVolumeSpacing, lesions, lesionOverlayOpacity, lesionOverlayVisibleMap],
  );

  /**
   * Fetch 3D triangle meshes for all configured lesions from the backend,
   * translate into CT phantom coordinates, and add as overlay on the volume.
   */
  const handleLoadLesionOverlays = useCallback(async () => {
    if (!phantom || !activeVolumeShape) {
      setLesionOverlayError('Generate a CT phantom first');
      return;
    }
    if (lesions.length === 0) {
      setLesionOverlayError('Add at least one lesion first');
      return;
    }

    const sp = activeVolumeSpacing ?? phantom.metadata.spacing;

    setLesionOverlayLoading(true);
    setLesionOverlayError(null);
    setLesionOverlayBaseMeshes([]);

    try {
      const results: LesionMeshData[] = [];

      for (let i = 0; i < lesions.length; i++) {
        const lesion = lesions[i];
        const radius = Math.max(...lesion.radiusMm);

        const request: Lesion3DPreviewRequest = {
          lesionType: lesion.type,
          shape: lesion.shape,
          centerX: 0,
          centerY: 0,
          centerZ: 0,
          radiusX: radius,
          radiusY: radius,
          radiusZ: radius,
          huMean: lesion.huMean,
          huStd: lesion.huStd,
          marginSharpness: lesion.marginSharpness,
          spiculationDegree: lesion.spiculationDegree,
          previewSize: 64,
          spacing: [sp[0], sp[1], sp[2]],
        };

        const mesh = await simulationService.previewLesion3D(request);

        // Translate mesh from preview volume space → CT phantom space
        const [depth, height, width] = activeVolumeShape;
        const lx = lesion.center[0] > 0 ? Math.min(lesion.center[0], width - 1) : width / 2;
        const ly = lesion.center[1] > 0 ? Math.min(lesion.center[1], height - 1) : height / 2;
        const lz = lesion.center[2] > 0 ? Math.min(lesion.center[2], depth - 1) : depth / 2;

        const [targetX, targetY, targetZ] = voxelToCenteredWorldMm(
          [lx, ly, lz],
          activeVolumeShape,
          sp,
        );

        const tx = targetX - mesh.center[0];
        const ty = targetY - mesh.center[1];
        const tz = targetZ - mesh.center[2];

        const translatedVertices = mesh.vertices.map(
          (v) => [v[0] + tx, v[1] + ty, v[2] + tz] as [number, number, number],
        );

        const color = LESION_OVERLAY_COLORS[i % LESION_OVERLAY_COLORS.length];

        results.push({
          id: `lesion-${i}`,
          vertices: translatedVertices,
          faces: mesh.faces,
          normals: mesh.normals,
          opacity: lesionOverlayOpacity,
          color: [color[0] / 255, color[1] / 255, color[2] / 255],
          visible: lesionOverlayVisibleMap[i] !== false,
        });
      }

      // Reset all to visible
      const visMap: Record<number, boolean> = {};
      for (let i = 0; i < lesions.length; i++) visMap[i] = true;
      setLesionOverlayVisibleMap(visMap);
      setLesionOverlayBaseMeshes(results);
    } catch (err: any) {
      console.error('[SimulationPage] Failed to load lesion overlays:', err);
      setLesionOverlayError(err?.message || 'Failed to load lesion overlays');
    } finally {
      setLesionOverlayLoading(false);
    }
  }, [phantom, activeVolumeShape, lesions, lesionOverlayOpacity, lesionOverlayVisibleMap]);

  /** Toggle visibility of a single lesion overlay */
  const handleToggleLesionVisibility = useCallback((index: number) => {
    setLesionOverlayVisibleMap((prev) => ({
      ...prev,
      [index]: prev[index] === false,
    }));
  }, []);

  /** Clear all lesion overlays and reset state */
  const handleClearLesionOverlays = useCallback(() => {
    setLesionOverlayBaseMeshes([]);
    setLesionOverlayVisibleMap({});
    setLesionOverlayError(null);
  }, []);

  // When lesions change, invalidate overlays (force re-fetch on next load)
  useEffect(() => {
    if (lesionOverlayBaseMeshes.length > 0) {
      // Only clear if the lesion count changed
      if (lesionOverlayBaseMeshes.length !== lesions.length) {
        handleClearLesionOverlays();
      }
    }
  }, [lesions.length, lesionOverlayBaseMeshes.length, handleClearLesionOverlays]);

  // -----------------------------------------------------------------------
  // Handlers: export
  // -----------------------------------------------------------------------

  /** Export simulation results */
  const handleExport = useCallback(async () => {
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

      const fallbackNames: Record<string, string> = {
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
  }, [latestCompletedJob, exportFormat]);

  // -----------------------------------------------------------------------
  // Render
  // -----------------------------------------------------------------------

  return (
    <div className="flex min-h-full flex-col">
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
        /*  Lesion / Organ / Deformation                                     */
        /* ================================================================ */
        <div className="flex-1 overflow-y-auto p-6">
          {/* Source Volume Selection */}
          <div className="mb-4 rounded-lg border border-border bg-card p-4">
            <h3 className="mb-3 text-sm font-medium">Source Volume</h3>
            <div className="flex gap-6">
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="sourceType"
                  checked={sourceType === 'synthetic'}
                  onChange={() => setSourceType('synthetic')}
                  className="text-primary"
                />
                Synthetic (empty volume)
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm">
                <input
                  type="radio"
                  name="sourceType"
                  checked={sourceType === 'dicom'}
                  onChange={() => setSourceType('dicom')}
                  className="text-primary"
                />
                Imported DICOM
              </label>
            </div>

            {sourceType === 'dicom' && (
              <div className="mt-3 grid gap-2 sm:grid-cols-2">
                {/* Study selector */}
                <div>
                  <label className="mb-1 block text-xs text-muted-foreground">Study</label>
                  <select
                    value={selectedStudyId ?? ''}
                    onChange={(e) => {
                      setSelectedStudyId(e.target.value || null);
                      setSelectedSeriesId(null);
                    }}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    disabled={loadingStudies}
                  >
                    <option value="">{loadingStudies ? 'Loading studies...' : '— Select a study —'}</option>
                    {studies.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.studyDescription || `Study ${s.studyDate}`} ({s.modalities?.join(', ') || '?'})
                      </option>
                    ))}
                  </select>
                </div>

                {/* Series selector */}
                <div>
                  <label className="mb-1 block text-xs text-muted-foreground">Series</label>
                  <select
                    value={selectedSeriesId ?? ''}
                    onChange={(e) => setSelectedSeriesId(e.target.value || null)}
                    className="w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                    disabled={!selectedStudyId || loadingSeries}
                  >
                    <option value="">
                      {!selectedStudyId
                        ? '— Select a study first —'
                        : loadingSeries
                          ? 'Loading series...'
                          : '— Select a series —'}
                    </option>
                    {seriesList.map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.seriesDescription || `Series ${s.seriesNumber}`} ({s.modality}, {s.imageCount} slices)
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {/* Configuration panel */}
          <div className="mb-6 grid gap-6 lg:grid-cols-2">
            {/* Lesion parameters */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="mb-4 text-sm font-medium">Lesion Parameters</h3>
              <div className="space-y-4">
                {/* Lesion Type */}
                <div>
                  <label className="text-xs text-muted-foreground">Lesion Type</label>
                  <select
                    value={form.lesionType}
                    onChange={(e) => updateForm('lesionType', e.target.value as LesionType)}
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  >
                    {Object.entries(lesionTypeLabel).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>

                {/* Shape */}
                <div>
                  <label className="text-xs text-muted-foreground">Shape</label>
                  <select
                    value={form.shape}
                    onChange={(e) => updateForm('shape', e.target.value as LesionShape)}
                    className="mt-1 w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
                  >
                    {Object.entries(shapeLabel).map(([value, label]) => (
                      <option key={value} value={value}>{label}</option>
                    ))}
                  </select>
                </div>

                {/* Mean HU */}
                <div>
                  <label className="text-xs text-muted-foreground">
                    Mean HU Value: <span className="text-primary font-mono">{form.huMean}</span>
                  </label>
                  <input
                    type="range"
                    min={-1000}
                    max={1000}
                    value={form.huMean}
                    onChange={(e) => updateForm('huMean', Number(e.target.value))}
                    className="mt-1 w-full"
                  />
                </div>

                {/* HU Std */}
                <div>
                  <label className="text-xs text-muted-foreground">
                    HU Heterogeneity (Std): <span className="text-primary font-mono">{form.huStd}</span>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={200}
                    value={form.huStd}
                    onChange={(e) => updateForm('huStd', Number(e.target.value))}
                    className="mt-1 w-full"
                  />
                </div>

                {/* Diameter */}
                <div>
                  <label className="text-xs text-muted-foreground">
                    Diameter (mm): <span className="text-primary font-mono">{form.diameter}</span>
                  </label>
                  <input
                    type="range"
                    min={2}
                    max={100}
                    value={form.diameter}
                    onChange={(e) => updateForm('diameter', Number(e.target.value))}
                    className="mt-1 w-full"
                  />
                </div>

                {/* Margin Sharpness (for advanced shapes) */}
                <div>
                  <label className="text-xs text-muted-foreground">
                    Margin Sharpness: <span className="text-primary font-mono">{form.marginSharpness.toFixed(2)}</span>
                  </label>
                  <input
                    type="range"
                    min={0}
                    max={100}
                    value={Math.round(form.marginSharpness * 100)}
                    onChange={(e) => updateForm('marginSharpness', Number(e.target.value) / 100)}
                    className="mt-1 w-full"
                  />
                </div>

                {/* --- Position controls --- */}
                <div className="border-t border-border pt-3">
                  <h4 className="mb-2 text-xs font-medium text-muted-foreground">
                    Position (voxel coordinates)
                  </h4>
                  <div className="grid grid-cols-3 gap-2">
                    <div>
                      <label className="text-xs text-muted-foreground">X</label>
                      <input
                        type="number"
                        value={form.centerX}
                        onChange={(e) => updateForm('centerX', Number(e.target.value))}
                        className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1 text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Y</label>
                      <input
                        type="number"
                        value={form.centerY}
                        onChange={(e) => updateForm('centerY', Number(e.target.value))}
                        className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1 text-sm"
                      />
                    </div>
                    <div>
                      <label className="text-xs text-muted-foreground">Z (Slice)</label>
                      <input
                        type="number"
                        value={form.centerZ}
                        onChange={(e) => updateForm('centerZ', Number(e.target.value))}
                        className="mt-0.5 w-full rounded border border-border bg-background px-2 py-1 text-sm"
                      />
                    </div>
                  </div>
                  <Button
                    variant="outline"
                    size="sm"
                    onClick={() => {
                      setPickedPosition(null);
                      setPickedWorldPositionMm(null);
                      setPickingMode(true);
                      setActiveTab('phantom');
                    }}
                    className="mt-2 w-full"
                    disabled={!phantom}
                  >
                    Pick from Phantom
                  </Button>
                  {!phantom && (
                    <p className="mt-1 text-[10px] text-muted-foreground">
                      Generate a CT phantom first to use position picking
                    </p>
                  )}
                </div>

                {/* Add Lesion button */}
                <Button onClick={handleAddLesion} className="w-full">
                  Add Lesion
                </Button>
              </div>
            </div>

            {/* Lesion list */}
            <div className="rounded-lg border border-border bg-card p-4">
              <h3 className="mb-4 text-sm font-medium">Lesion List ({lesions.length})</h3>
              {lesions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No lesions configured. Adjust parameters above and click <strong>Add Lesion</strong>.
                </p>
              ) : (
                <div className="space-y-2 max-h-[420px] overflow-y-auto">
                  {lesions.map((lesion, index) => (
                    <div
                      key={index}
                      className="group flex items-start justify-between rounded border border-border p-2 text-sm"
                    >
                      <div className="min-w-0 flex-1">
                        <div className="font-medium">
                          Lesion {index + 1}: {lesionTypeLabel[lesion.type]} ({shapeLabel[lesion.shape]})
                        </div>
                        <div className="mt-0.5 text-xs text-muted-foreground">
                          HU: {lesion.huMean} ± {lesion.huStd} · Ø {Math.round(lesion.radiusMm[0] * 2)} mm
                          {lesion.spiculationDegree > 0 && ` · Spic: ${lesion.spiculationDegree.toFixed(1)}`}
                        </div>
                      </div>
                      {lesionOverlayBaseMeshes.length > 0 && (
                        <button
                          onClick={() => handleToggleLesionVisibility(index)}
                          className={`shrink-0 rounded p-1 text-sm transition-opacity ${
                            lesionOverlayVisibleMap[index] !== false
                              ? 'text-primary opacity-100'
                              : 'text-muted-foreground/30 opacity-50'
                          }`}
                          title={lesionOverlayVisibleMap[index] !== false ? 'Hide lesion in 3D view' : 'Show lesion in 3D view'}
                        >
                          {lesionOverlayVisibleMap[index] !== false ? '◉' : '○'}
                        </button>
                      )}
                      <button
                        onClick={() => handleRemoveLesion(index)}
                        className="ml-1 shrink-0 rounded p-1 text-muted-foreground opacity-0 transition-opacity hover:bg-destructive/10 hover:text-destructive group-hover:opacity-100"
                        title="Remove lesion"
                      >
                        <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* Action buttons */}
          <div className="mt-auto flex flex-wrap items-center gap-2 border-t border-border pt-4">
            {/* Run Simulation */}
            <Button
              variant="default"
              onClick={handleRunSimulation}
              disabled={jobLoading || (lesions.length === 0 && organs.length === 0)}
            >
              {jobLoading ? 'Creating Job...' : 'Run Simulation'}
            </Button>

            {/* Preview */}
            <Button
              variant="outline"
              onClick={handlePreview}
              disabled={previewLoading}
            >
              {previewLoading ? 'Previewing...' : 'Preview'}
            </Button>

            {/* 3D Preview */}
            <Button
              variant="outline"
              onClick={handlePreview3D}
              disabled={meshPreviewLoading}
            >
              {meshPreviewLoading ? 'Generating 3D...' : '3D Preview'}
            </Button>

            {/* Preview on CT — only when DICOM source is selected */}
            {sourceType === 'dicom' && selectedSeriesId && (
              <Button
                variant="outline"
                onClick={handleDicomPreview}
                disabled={dicomPreviewLoading}
              >
                {dicomPreviewLoading ? 'Loading CT Preview...' : 'Preview on CT'}
              </Button>
            )}

            {/* Preview result */}
            {previewResult && (
              <span className="ml-2 text-xs text-green-600">{previewResult}</span>
            )}
            {previewError && (
              <span className="ml-2 text-xs text-destructive">{previewError}</span>
            )}
            {dicomPreviewError && (
              <span className="ml-2 text-xs text-destructive">{dicomPreviewError}</span>
            )}

            {/* Spacer */}
            <div className="flex-1" />

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
                disabled={exporting || !latestCompletedJob}
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

          {/* Active / completed job summary */}
          {(activeJobs.length > 0 || completedJobs.length > 0) && (
            <div className="mt-4 border-t border-border pt-3 text-xs text-muted-foreground">
              {activeJobs.length > 0 && (
                <div>
                  Active jobs: {activeJobs.map((j) => `${j.id.slice(0, 8)} (${j.status})`).join(', ')}
                </div>
              )}
              {completedJobs.length > 0 && (
                <div>Completed jobs: {completedJobs.length}</div>
              )}
            </div>
          )}
        </div>
      ) : (
        /* ================================================================ */
        /*  CT Phantom View                                                  */
        /* ================================================================ */
        <div className="flex-1 overflow-y-auto p-6">
          <div className="flex flex-col gap-4 pb-6">
            <div className="rounded-[28px] border border-border/70 bg-gradient-to-br from-slate-950 via-slate-900 to-zinc-950 p-4 shadow-[0_24px_80px_rgba(0,0,0,0.28)]">
              <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
                <div>
                  <div className="text-[11px] font-medium uppercase tracking-[0.24em] text-cyan-300/70">
                    CT Simulation Workspace
                  </div>
                  <h3 className="mt-1 text-lg font-semibold text-white">Clean axial browsing with one synchronized 3D view</h3>
                  <p className="mt-1 text-sm text-slate-300/70">
                    Slice view and accumulated volume stay on the same active stack without duplicate comparison panels.
                  </p>
                </div>

                <div className="flex flex-wrap gap-2">
                  <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-slate-200/80">
                    {activeVolumeSourceLabel}
                  </span>
                  {activeVolumeShape && (
                    <span className="rounded-full border border-white/10 bg-white/5 px-3 py-1 text-[11px] text-slate-200/80">
                      {activeVolumeShape[2]} x {activeVolumeShape[1]} x {activeVolumeShape[0]}
                    </span>
                  )}
                  {activeSpacingLabel && (
                    <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-[11px] text-cyan-100/80">
                      spacing {activeSpacingLabel} mm
                    </span>
                  )}
                </div>
              </div>

              <div className="grid gap-4 xl:grid-cols-[minmax(0,1.18fr)_minmax(360px,0.82fr)]">
                <div className="flex min-h-[620px] flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/70 backdrop-blur">
                  <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-4 py-3">
                    <div>
                      <div className="text-xs font-semibold uppercase tracking-[0.18em] text-white/55">
                        Axial Slice
                      </div>
                      <div className="mt-1 text-sm text-white/85">
                        {activeVolumeShape ? `Slice ${sliceIndex + 1} / ${totalSlices}` : 'Ready for CT load'}
                      </div>
                    </div>
                    <div className="flex flex-wrap items-center gap-2 text-[11px]">
                      {activePickedPosition && (
                        <span className="rounded-full border border-red-400/20 bg-red-400/10 px-2.5 py-1 text-red-200/80">
                          Pick ({activePickedPosition.x}, {activePickedPosition.y}, z={activePickedPosition.z})
                        </span>
                      )}
                      {pickedWorldPositionMm && (
                        <span className="rounded-full border border-emerald-400/20 bg-emerald-400/10 px-2.5 py-1 text-emerald-200/80">
                          World ({pickedWorldPositionMm[0].toFixed(1)}, {pickedWorldPositionMm[1].toFixed(1)}, {pickedWorldPositionMm[2].toFixed(1)}) mm
                        </span>
                      )}
                      {pickingMode && (
                        <span className="rounded-full border border-amber-400/20 bg-amber-400/10 px-2.5 py-1 text-amber-200">
                          Click canvas to set position
                        </span>
                      )}
                    </div>
                  </div>

                  <div className="flex min-h-0 flex-1 items-center justify-center p-5">
                    {!phantom ? (
                      <div className="flex max-w-sm flex-col items-center gap-3 text-center">
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
                          Load an atlas, procedural, or DICOM CT volume to start browsing.
                        </p>
                        {phantomLoading && (
                          <div className="flex items-center gap-2 text-xs text-white/50">
                            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            Loading CT volume...
                          </div>
                        )}
                        {phantomError && (
                          <p className="text-xs text-red-400">{phantomError}</p>
                        )}
                      </div>
                    ) : activeVolumeShape ? (
                      <div
                        className="flex max-h-full w-full items-center justify-center rounded-xl border border-white/8 bg-neutral-950/80 p-3"
                        style={{ aspectRatio: `${activeVolumeShape[2]} / ${activeVolumeShape[1]}` }}
                      >
                        <canvas
                          ref={canvasRef}
                          onClick={handleCanvasClick}
                          className={`h-auto max-h-full w-full max-w-full rounded-lg border border-white/10 ${
                            pickingMode ? 'cursor-crosshair' : 'cursor-default'
                          }`}
                          style={{ imageRendering: 'pixelated' }}
                        />
                      </div>
                    ) : null}
                  </div>
                </div>

                <div className="grid min-h-[620px] gap-4 grid-rows-[minmax(0,1fr)_auto]">
                  <div className="flex min-h-0 flex-col overflow-hidden rounded-2xl border border-white/10 bg-black/80">
                    <div className="flex items-center justify-between gap-2 border-b border-white/10 px-4 py-3">
                      <div>
                        <div className="text-xs font-semibold uppercase tracking-[0.18em] text-white/55">
                          3D Volume
                        </div>
                        <div className="mt-1 text-sm text-white/85">
                          Original CT rendering with segmentation color overlay
                        </div>
                      </div>
                      <div className="flex items-center gap-2">
                        <label className="flex items-center gap-1.5 text-[11px] text-white/60 cursor-pointer select-none">
                          <input
                            type="checkbox"
                            checked={sync3DToSlice}
                            onChange={(e) => setSync3DToSlice(e.target.checked)}
                            className="accent-primary"
                          />
                          Slice Sync
                        </label>
                        {ctParamsLoading && (
                          <span className="rounded-full border border-cyan-400/20 bg-cyan-400/10 px-2.5 py-1 text-[11px] text-cyan-200/85">
                            Running simulation...
                          </span>
                        )}
                      </div>
                    </div>

                    <div className="relative min-h-0 flex-1">
                      {vtkVolumeData && vtkDims ? (
                        <>
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
                            syntheticClipIndex={sync3DToSlice ? sliceIndex : undefined}
                            syntheticClipDirection="low_to_high"
                            syntheticScanAxis="z"
                            segmentationMask={compositeSegmentationOverlay?.mask ?? null}
                            segmentationLabels={compositeSegmentationOverlay?.labels ?? null}
                            lesionMeshes={lesionOverlayMeshes.length > 0 ? lesionOverlayMeshes : null}
                          />
                          <SlicePositionIndicator
                            sliceIndex={sliceIndex}
                            scanStartIndex={scanStartIndex}
                            scanEndIndex={scanEndIndex}
                          />
                        </>
                      ) : (
                        <div className="flex h-full items-center justify-center px-6 text-center">
                          <p className="max-w-xs text-sm text-white/30">
                            Load a CT volume to view the synchronized 3D accumulation.
                          </p>
                        </div>
                      )}
                    </div>
                  </div>

                  <div className="grid gap-3 sm:grid-cols-3">
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                      <div className="text-[11px] uppercase tracking-[0.16em] text-white/45">Current Stack</div>
                      <div className="mt-1 text-sm font-medium text-white/85">{activeVolumeSourceLabel}</div>
                      <div className="mt-1 text-xs text-white/45">
                        {ctParamsResult
                          ? 'Showing the latest CT-parameter result at the current slice.'
                          : `Showing the loaded ${activeVolumeSourceLabel.toLowerCase()}.`}
                        {phantom?.metadata.source === 'dicom'
                          && selectedSeriesId
                          && loadedWorkspaceSeriesId
                          && selectedSeriesId !== loadedWorkspaceSeriesId
                          ? ' Selected DICOM series has changed; reload CT volume to simulate the newly selected series.'
                          : ''}
                      </div>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                      <div className="text-[11px] uppercase tracking-[0.16em] text-white/45">Window</div>
                      <div className="mt-1 text-sm font-medium text-white/85">
                        WL {activePreset.windowLevel} / WW {activePreset.windowWidth}
                      </div>
                      <div className="mt-1 text-xs text-white/45">{activePreset.label} preset</div>
                    </div>
                    <div className="rounded-2xl border border-white/10 bg-white/[0.03] px-4 py-3">
                      <div className="text-[11px] uppercase tracking-[0.16em] text-white/45">Playback</div>
                      <div className="mt-1 text-sm font-medium text-white/85">
                        {sync3DToSlice ? `${playSpeed} fps` : 'Full Volume'}
                      </div>
                      <div className="mt-1 text-xs text-white/45">
                        {sync3DToSlice
                          ? (playing ? 'Auto-scrolling slices' : 'Manual slice-synced accumulation')
                          : 'Showing the full CT volume'}
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* ---- Compact toolbar ---- */}
            <div className="rounded-2xl border border-border/70 bg-card px-4 py-3 shadow-sm">
              <div className="flex flex-wrap items-center gap-4">
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Source:</span>
                  {(['atlas', 'procedural', 'dicom'] as const).map((source) => (
                    <button
                      key={source}
                      type="button"
                      onClick={() => {
                        setCtWorkspaceSource(source);
                        setCtParamsResult(null);
                        setCtParamsError(null);
                        setPhantomError(null);
                      }}
                      className={`rounded px-2 py-1 text-xs transition-colors ${
                        ctWorkspaceSource === source
                          ? 'bg-primary text-primary-foreground'
                          : 'bg-muted text-muted-foreground hover:bg-muted-foreground/20'
                      }`}
                      disabled={phantomLoading}
                    >
                      {source}
                    </button>
                  ))}
                </div>

                {ctWorkspaceSource === 'atlas' && (
                  <>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Atlas:</span>
                      <select
                        value={selectedAtlasCaseId}
                        onChange={(e) => {
                          setSelectedAtlasCaseId(e.target.value);
                          setCtParamsResult(null);
                          setPhantomError(null);
                        }}
                        disabled={loadingAtlasCases || phantomLoading || atlasCases.length === 0}
                        className="max-w-[220px] rounded border border-border bg-background px-2 py-1 text-xs"
                      >
                        {atlasCases.length === 0 ? (
                          <option value="LUNG1-001">
                            {loadingAtlasCases ? 'Loading atlas cases...' : 'No atlas cases found'}
                          </option>
                        ) : (
                          atlasCases.map((atlasCase) => (
                            <option key={atlasCase.caseId} value={atlasCase.caseId}>
                              {atlasCase.label}
                            </option>
                          ))
                        )}
                      </select>
                    </div>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void refreshAtlasCases()}
                      disabled={loadingAtlasCases || phantomLoading}
                    >
                      {loadingAtlasCases ? 'Refreshing...' : 'Refresh Atlas'}
                    </Button>
                  </>
                )}

                {ctWorkspaceSource === 'dicom' && (
                  <>
                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Study:</span>
                      <select
                        value={selectedStudyId ?? ''}
                        onChange={(e) => {
                          setSelectedStudyId(e.target.value || null);
                          setSelectedSeriesId(null);
                          setCtParamsResult(null);
                        }}
                        disabled={loadingStudies || phantomLoading}
                        className="max-w-[220px] rounded border border-border bg-background px-2 py-1 text-xs"
                      >
                        <option value="">{loadingStudies ? 'Loading studies...' : 'Select study'}</option>
                        {studies.map((study) => (
                          <option key={study.id} value={study.id}>
                            {study.patientName || study.patientId || study.studyInstanceUid || study.id}
                          </option>
                        ))}
                      </select>
                    </div>

                    <div className="flex items-center gap-2">
                      <span className="text-xs text-muted-foreground">Series:</span>
                      <select
                        value={selectedSeriesId ?? ''}
                        onChange={(e) => {
                          setSelectedSeriesId(e.target.value || null);
                          setCtParamsResult(null);
                        }}
                        disabled={!selectedStudyId || loadingSeries || phantomLoading}
                        className="max-w-[260px] rounded border border-border bg-background px-2 py-1 text-xs"
                      >
                        <option value="">
                          {!selectedStudyId
                            ? 'Select study first'
                            : loadingSeries
                              ? 'Loading series...'
                              : 'Select CT series'}
                        </option>
                        {ctWorkspaceSeriesList.map((series) => (
                          <option key={series.id} value={series.id}>
                            {series.seriesNumber ? `#${series.seriesNumber} ` : ''}{series.seriesDescription || series.modality || series.id}
                          </option>
                        ))}
                      </select>
                    </div>

                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => void refreshDicomStudies()}
                      disabled={loadingStudies || phantomLoading}
                    >
                      {loadingStudies ? 'Refreshing...' : 'Refresh DICOM'}
                    </Button>
                  </>
                )}

                {/* Size selector */}
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">Size:</span>
                  <select
                    value={phantomSize}
                    onChange={(e) => {
                      setPhantomSize(Number(e.target.value));
                      setCtParamsResult(null);
                      setCtParamsError(null);
                    }}
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
                  ? 'Loading...'
                  : ctWorkspaceSource === 'atlas'
                    ? 'Load Atlas CT'
                    : ctWorkspaceSource === 'procedural'
                      ? 'Load Procedural CT'
                      : 'Load DICOM CT'}
              </Button>

              {/* Play / Pause */}
              <Button
                variant="outline"
                size="sm"
                onClick={handlePlayPause}
                disabled={!phantom}
              >
                {playing ? 'Pause' : 'Play'}
              </Button>

              {/* Slice slider */}
              <div className="flex min-w-[240px] flex-1 items-center gap-2">
                <span className="text-xs text-muted-foreground w-16 tabular-nums">
                  {phantom
                    ? `${sliceIndex + 1} / ${totalSlices}`
                    : '-- / --'}
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

              {/* Pick Position toggle */}
              <div className="h-5 w-px bg-border" />
              <Button
                variant={pickingMode ? 'default' : 'outline'}
                size="sm"
                onClick={() => setPickingMode((prev) => !prev)}
                disabled={!phantom}
              >
                {pickingMode ? 'Cancel Pick' : 'Pick Position'}
              </Button>

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

              {/* Phase 5: Lesion mesh overlay controls */}
              {phantom && lesions.length > 0 && (
                <>
                  <div className="h-5 w-px bg-border" />
                  {lesionOverlayBaseMeshes.length > 0 ? (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleClearLesionOverlays}
                      >
                        Clear Lesions
                      </Button>
                      <div className="flex items-center gap-1.5">
                        <span className="text-[10px] text-muted-foreground/70 whitespace-nowrap">Opacity</span>
                        <input
                          type="range"
                          min={0.05}
                          max={1}
                          step={0.05}
                          value={lesionOverlayOpacity}
                          onChange={(e) => setLesionOverlayOpacity(Number(e.target.value))}
                          className="h-1 w-20 cursor-pointer accent-primary"
                        />
                        <span className="w-8 text-right text-[10px] tabular-nums text-muted-foreground/60">
                          {Math.round(lesionOverlayOpacity * 100)}%
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="flex items-center gap-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleLoadLesionOverlays}
                        disabled={lesionOverlayLoading}
                      >
                        {lesionOverlayLoading ? (
                          <span className="flex items-center gap-1">
                            <div className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                            Loading...
                          </span>
                        ) : (
                          `Load ${lesions.length} Lesion${lesions.length > 1 ? 's' : ''}`
                        )}
                      </Button>
                      {lesionOverlayError && (
                        <span className="text-[10px] text-red-400">{lesionOverlayError}</span>
                      )}
                    </div>
                  )}
                </>
              )}

              {/* Source badge */}
              {phantom && phantom.metadata.source && (
                <>
                  <div className="h-5 w-px bg-border" />
                  <span className="rounded bg-muted px-1.5 py-0.5 text-xs text-muted-foreground">
                    {phantom.metadata.source === 'atlas'
                      ? `atlas · ${phantom.metadata.caseId || '—'}`
                      : phantom.metadata.source === 'dicom'
                        ? `dicom · ${ctParamsResult?.metadata.seriesId || loadedWorkspaceSeriesId || 'loaded series'}`
                        : 'procedural'}
                  </span>
                </>
              )}
              </div>
            </div>

            <div className="rounded-lg border border-border bg-card">
              <div className="flex items-center justify-between gap-3 px-4 py-3">
                <h3 className="text-sm font-medium">CT Scan Params Simulation</h3>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => setIsCtParamsPanelOpen((prev) => !prev)}
                >
                  {isCtParamsPanelOpen ? 'Collapse' : 'Expand'}
                </Button>
              </div>

              {isCtParamsPanelOpen && (
                <div className="border-t border-border/60 px-4 py-4">
                  <div className="mb-3 flex flex-wrap items-start justify-between gap-3">
                    <p className="max-w-3xl text-xs text-muted-foreground">
                      CT parameter simulation runs on the currently loaded CT volume. Parameter changes are applied only when you click the run button. Angle controls support pitch, yaw, and roll.
                    </p>
                    <Button
                      variant="default"
                      size="sm"
                      onClick={() => {
                        void handleRunCtParamsSimulation();
                      }}
                      disabled={ctParamsLoading}
                    >
                      {ctParamsLoading ? 'Running...' : 'Run CT Parameter Simulation'}
                    </Button>
                  </div>

                  <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                    <div className="flex items-center justify-between gap-3 md:col-span-2 xl:col-span-3">
                      <div>
                        <h4 className="text-xs font-medium uppercase tracking-wide text-foreground/80">
                          Gantry Angles
                        </h4>
                        <p className="text-[11px] text-muted-foreground/80">
                          Reset all three axes to the original 0° pose.
                        </p>
                      </div>
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={handleResetCtAngles}
                      >
                        Reset Angles
                      </Button>
                    </div>

                    <label className="flex flex-col gap-1 text-xs text-muted-foreground md:col-span-2 xl:col-span-3">
                    Pitch (deg)
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryPitchDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryPitchDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="flex-1 cursor-pointer accent-primary"
                      />
                      <input
                        type="number"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryPitchDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryPitchDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="w-20 rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                      />
                      <span className="w-8 text-right text-xs text-muted-foreground">
                        {ctParams.gantryPitchDeg}°
                      </span>
                    </div>
                    <span className="text-[11px] text-muted-foreground/80">
                      Range: -30° to 30°. Rotation is applied around the patient left-right axis.
                    </span>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Yaw (deg)
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryYawDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryYawDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="flex-1 cursor-pointer accent-primary"
                      />
                      <input
                        type="number"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryYawDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryYawDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="w-20 rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                      />
                    </div>
                    <span className="text-[11px] text-muted-foreground/80">
                      Range: -30° to 30°. Left/right turning around the anterior-posterior axis.
                    </span>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Roll (deg)
                    <div className="flex items-center gap-3">
                      <input
                        type="range"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryRollDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryRollDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="flex-1 cursor-pointer accent-primary"
                      />
                      <input
                        type="number"
                        min={-30}
                        max={30}
                        step={1}
                        value={ctParams.gantryRollDeg}
                        onChange={(e) =>
                          setCtParams((prev) => ({
                            ...prev,
                            gantryRollDeg: Math.max(-30, Math.min(30, Number(e.target.value) || 0)),
                          }))
                        }
                        className="w-20 rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                      />
                    </div>
                    <span className="text-[11px] text-muted-foreground/80">
                      Range: -30° to 30°. Side tilt around the head-to-feet axis.
                    </span>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Slice Thickness (mm)
                    <select
                      value={ctParams.sliceThicknessMm}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          sliceThicknessMm: Number(e.target.value) as CtParamsPreviewParams['sliceThicknessMm'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {[0.625, 1.0, 2.5, 5.0, 10.0, 15.0, 20.0].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Dose Level
                    <select
                      value={ctParams.doseLevel}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          doseLevel: e.target.value as CtParamsPreviewParams['doseLevel'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {['low', 'standard', 'high'].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    mAs
                    <input
                      type="number"
                      min={MIN_MAS}
                      max={MAX_MAS}
                      step={1}
                      value={mAsInput}
                      onChange={(e) => setMAsInput(e.target.value)}
                      onBlur={normalizeMAsInput}
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    />
                    <span className="text-[11px] text-muted-foreground/80">mAs range: 30-300</span>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    kVp
                    <select
                      value={ctParams.kVp}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          kVp: Number(e.target.value) as CtParamsPreviewParams['kVp'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {[80, 100, 120, 140].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Pitch
                    <select
                      value={ctParams.pitch}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          pitch: Number(e.target.value) as CtParamsPreviewParams['pitch'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {[0.5, 0.8, 1.0, 1.2, 1.5].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    FOV (mm)
                    <select
                      value={ctParams.fovMm}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          fovMm: Number(e.target.value) as CtParamsPreviewParams['fovMm'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {[150, 250, 350, 500].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Matrix Size
                    <select
                      value={ctParams.matrixSize}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          matrixSize: Number(e.target.value) as CtParamsPreviewParams['matrixSize'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {[256, 512, 1024].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Kernel
                    <select
                      value={ctParams.kernel}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          kernel: e.target.value as CtParamsPreviewParams['kernel'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {['smooth', 'soft', 'standard', 'lung', 'bone', 'sharp'].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>

                  <label className="flex flex-col gap-1 text-xs text-muted-foreground">
                    Contrast Phase
                    <select
                      value={ctParams.contrastPhase}
                      onChange={(e) =>
                        setCtParams((prev) => ({
                          ...prev,
                          contrastPhase: e.target.value as CtParamsPreviewParams['contrastPhase'],
                        }))
                      }
                      className="rounded border border-border bg-background px-2 py-1.5 text-sm text-foreground"
                    >
                      {['noncontrast', 'arterial', 'venous', 'delayed'].map((value) => (
                        <option key={value} value={value}>
                          {value}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>

                  {ctParamsError && (
                    <p className="mt-3 text-xs text-red-500">{ctParamsError}</p>
                  )}

                  {ctParamsResult && (
                    <div className="mt-4 rounded border border-border/70 bg-background/60 p-3">
                      <div className="mb-3 flex items-center justify-between gap-3">
                        <div>
                          <h4 className="text-sm font-medium">Simulation Result</h4>
                          <p className="text-xs text-muted-foreground">
                            Preview metadata and the exact parameter payload returned by the backend.
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <Button variant="outline" size="sm" onClick={handleCopyCtParamsJson}>
                            {ctParamsCopyState || 'Copy Params JSON'}
                          </Button>
                          <Button variant="outline" size="sm" onClick={handleDownloadCtParamsJson}>
                            {ctParamsDownloadState || 'Download Params JSON'}
                          </Button>
                        </div>
                      </div>

                      <div className="grid gap-2 sm:grid-cols-2">
                        <div className="rounded border border-border/70 bg-card px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">HU Range</div>
                          <div className="mt-1 text-xs text-foreground">
                            Before: {ctParamsResult.paramsJson.huRangeBefore ? `${ctParamsResult.paramsJson.huRangeBefore[0].toFixed(1)} to ${ctParamsResult.paramsJson.huRangeBefore[1].toFixed(1)}` : '--'}
                          </div>
                          <div className="text-xs text-foreground">
                            After: {ctParamsResult.paramsJson.huRangeAfter ?? ctParamsResult.metadata.huRange ? `${(ctParamsResult.paramsJson.huRangeAfter ?? ctParamsResult.metadata.huRange)[0].toFixed(1)} to ${(ctParamsResult.paramsJson.huRangeAfter ?? ctParamsResult.metadata.huRange)[1].toFixed(1)}` : '--'}
                          </div>
                        </div>

                        <div className="rounded border border-border/70 bg-card px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Shape</div>
                          <div className="mt-1 text-xs text-foreground">
                            Input: {ctParamsResult.paramsJson.inputShape ? ctParamsResult.paramsJson.inputShape.join(' x ') : '--'}
                          </div>
                          <div className="text-xs text-foreground">
                            Output: {(ctParamsResult.paramsJson.outputShape ?? ctParamsResult.metadata.shape) ? (ctParamsResult.paramsJson.outputShape ?? ctParamsResult.metadata.shape).join(' x ') : '--'}
                          </div>
                        </div>
                      </div>

                      <div className="mt-2 grid gap-2 sm:grid-cols-2">
                        <div className="rounded border border-border/70 bg-card px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Effective Slice Thickness</div>
                          <div className="mt-1 text-xs text-foreground">
                            {ctParamsResult.metadata.effectiveSliceThicknessMm.toFixed(3)} mm
                          </div>
                        </div>

                        <div className="rounded border border-border/70 bg-card px-3 py-2">
                          <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Warnings</div>
                          <div className="mt-1 text-xs text-foreground">
                            {(ctParamsResult.metadata.warnings ?? ctParamsResult.paramsJson.warnings ?? []).length > 0
                              ? (ctParamsResult.metadata.warnings ?? ctParamsResult.paramsJson.warnings ?? []).join(' | ')
                              : 'None'}
                          </div>
                        </div>
                      </div>

                      {originalCenterSliceStats && simulatedCenterSliceStats && (
                        <div className="mt-2 grid gap-2 sm:grid-cols-2">
                          <div className="rounded border border-border/70 bg-card px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Center Slice Before</div>
                            <div className="mt-1 text-xs text-foreground">
                              Slice {originalCenterSliceStats.sliceIndex + 1} · mean {originalCenterSliceStats.mean.toFixed(1)} HU · std {originalCenterSliceStats.std.toFixed(1)}
                            </div>
                            <div className="text-xs text-foreground">
                              Min/Max: {originalCenterSliceStats.min.toFixed(1)} / {originalCenterSliceStats.max.toFixed(1)}
                            </div>
                          </div>

                          <div className="rounded border border-border/70 bg-card px-3 py-2">
                            <div className="text-[11px] uppercase tracking-wide text-muted-foreground">Center Slice After</div>
                            <div className="mt-1 text-xs text-foreground">
                              Slice {simulatedCenterSliceStats.sliceIndex + 1} · mean {simulatedCenterSliceStats.mean.toFixed(1)} HU · std {simulatedCenterSliceStats.std.toFixed(1)}
                            </div>
                            <div className="text-xs text-foreground">
                              Min/Max: {simulatedCenterSliceStats.min.toFixed(1)} / {simulatedCenterSliceStats.max.toFixed(1)}
                            </div>
                          </div>
                        </div>
                      )}

                      {ctParamsResult.standardizedCase && (
                        <div className="mt-3 rounded border border-border/70 bg-card px-3 py-2">
                          <div className="mb-2 flex items-center justify-between gap-3">
                            <div>
                              <div className="text-xs font-medium text-foreground">Standardized Output for Downstream Modules</div>
                              <div className="text-[11px] text-muted-foreground">
                                Lightweight standardized case metadata for downstream artifact or lesion modules.
                              </div>
                            </div>
                            <div className="flex items-center gap-2">
                              <Button variant="outline" size="sm" onClick={handleCopyStandardizedCaseJson}>
                                {standardizedCaseCopyState || 'Copy Standardized Case JSON'}
                              </Button>
                              <Button variant="outline" size="sm" onClick={handleDownloadStandardizedCaseJson}>
                                {standardizedCaseDownloadState || 'Download Standardized Case JSON'}
                              </Button>
                            </div>
                          </div>

                          <div className="grid gap-2 text-xs text-foreground sm:grid-cols-2 xl:grid-cols-3">
                            <div><span className="text-muted-foreground">case_id:</span> {ctParamsResult.standardizedCase.caseId}</div>
                            <div><span className="text-muted-foreground">source:</span> {ctParamsResult.standardizedCase.source}</div>
                            <div><span className="text-muted-foreground">shape:</span> {ctParamsResult.standardizedCase.volume.shape.join(' x ')}</div>
                            <div><span className="text-muted-foreground">spacing:</span> {ctParamsResult.standardizedCase.volume.spacing.join(', ')}</div>
                            <div><span className="text-muted-foreground">spatial_reference:</span> {ctParamsResult.standardizedCase.volume.spatialReference ?? 'local_volume_space'}</div>
                            <div><span className="text-muted-foreground">hu_range:</span> {ctParamsResult.standardizedCase.volume.huRange[0].toFixed(1)} to {ctParamsResult.standardizedCase.volume.huRange[1].toFixed(1)}</div>
                            <div><span className="text-muted-foreground">slice_count:</span> {ctParamsResult.standardizedCase.volume.sliceCount}</div>
                            <div><span className="text-muted-foreground">dtype:</span> {ctParamsResult.standardizedCase.volume.dtype}</div>
                            <div><span className="text-muted-foreground">axis_order:</span> {ctParamsResult.standardizedCase.volume.axisOrder}</div>
                            <div><span className="text-muted-foreground">image_data_field:</span> {ctParamsResult.standardizedCase.volume.imageDataField}</div>
                          </div>
                        </div>
                      )}

                      <details className="mt-3 rounded border border-border/70 bg-card px-3 py-2">
                        <summary className="cursor-pointer text-xs font-medium text-foreground">
                          Params JSON
                        </summary>
                        <pre className="mt-2 overflow-auto whitespace-pre-wrap break-all text-[11px] text-muted-foreground">
                          {JSON.stringify(ctParamsResult.paramsJson, null, 2)}
                        </pre>
                      </details>
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* ---- Label legend (compact, shown when labels available) ---- */}
            {phantom?.labelBase64 && showLabelOverlay && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/50 pt-2">
                <span className="text-[10px] text-muted-foreground/60">
                  Labels:
                </span>
                {visibleLabelLegendItems.map(({ index, name, color }) => {
                  return (
                    <span
                      key={index}
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

            {/* ---- Lesion overlay color legend ---- */}
            {lesionOverlayMeshes.length > 0 && (
              <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 border-t border-border/50 pt-2">
                <span className="text-[10px] text-muted-foreground/60">
                  Lesions:
                </span>
                {lesionOverlayMeshes.map((mesh, i) => {
                  const c = LESION_OVERLAY_COLORS[i % LESION_OVERLAY_COLORS.length];
                  return (
                    <span
                      key={mesh.id}
                      className="flex items-center gap-1 text-[10px] text-muted-foreground"
                    >
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-sm border border-white/20"
                        style={{ backgroundColor: `rgb(${c[0]},${c[1]},${c[2]})` }}
                      />
                      {lesionTypeLabel[lesions[i]?.type] || `Lesion ${i + 1}`}
                      {!mesh.visible && (
                        <span className="text-muted-foreground/40">(hidden)</span>
                      )}
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

      {/* ---- DICOM Preview Modal ---- */}
      {dicomPreviewImage && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={closeDicomPreview}
        >
          <div
            className="relative max-h-[90vh] max-w-[90vw] rounded-lg bg-card p-4 shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={closeDicomPreview}
              className="absolute -right-3 -top-3 flex h-7 w-7 items-center justify-center rounded-full bg-destructive text-sm text-destructive-foreground hover:bg-destructive/90"
              title="Close"
            >
              ✕
            </button>
            <h3 className="mb-3 text-sm font-medium">CT Preview — Lesion on DICOM</h3>
            <p className="mb-2 text-xs text-muted-foreground">
              Left: Original · Right: With lesion (center slice, WW/WC: 400/40)
            </p>
            <img
              src={dicomPreviewImage}
              alt="DICOM lesion preview"
              className="max-h-[65vh] w-auto rounded border border-border"
            />
            {dicomPreviewStats && (
              <p className="mt-2 text-xs text-green-600">{dicomPreviewStats}</p>
            )}
          </div>
        </div>
      )}

      {/* ---- 3D Lesion Mesh Preview Modal ---- */}
      {meshPreviewOpen && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/60"
          onClick={closeMeshPreview}
        >
          <div
            className="relative flex h-[85vh] w-[85vw] flex-col overflow-hidden rounded-lg bg-card shadow-xl"
            onClick={(e) => e.stopPropagation()}
          >
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-5 py-3">
              <div>
                <h3 className="text-sm font-medium">3D Body Preview with Lesion</h3>
                <p className="text-xs text-muted-foreground">
                  {form.shape} · {lesionTypeLabel[form.lesionType]} · Ø {form.diameter} mm
                </p>
              </div>
              <div className="flex items-center gap-3">
                {meshPreviewLoading && (
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <div className="h-3 w-3 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                    Generating body with lesion...
                  </div>
                )}
                {meshPreviewError && (
                  <span className="text-xs text-destructive">{meshPreviewError}</span>
                )}
                {meshPreviewData && (
                  <span className="rounded bg-primary/10 px-2 py-0.5 text-[11px] text-primary">
                    {meshPreviewData.vertices.length} vertices · {meshPreviewData.faces.length} triangles
                  </span>
                )}
                <button
                  onClick={closeMeshPreview}
                  className="flex h-7 w-7 items-center justify-center rounded-full bg-destructive text-sm text-destructive-foreground hover:bg-destructive/90"
                  title="Close"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* VolumeRenderer canvas — CT phantom body with lesion mesh overlay */}
            <div className="relative flex-1 bg-black">
              {previewPhantomData && previewPhantomDims ? (
                <VolumeRenderer
                  mode="synthetic"
                  showControls
                  syntheticData={previewPhantomData}
                  syntheticDims={previewPhantomDims}
                  syntheticSpacing={previewPhantomSpacing}
                  lesionMeshes={meshPreviewData ? [meshPreviewData] : null}
                />
              ) : (
                <div className="flex h-full items-center justify-center">
                  {meshPreviewLoading ? (
                    <div className="flex flex-col items-center gap-2">
                      <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
                      <span className="text-xs text-muted-foreground">Generating phantom with lesion...</span>
                    </div>
                  ) : meshPreviewError ? (
                    <div className="text-center">
                      <p className="mb-1 text-sm font-medium text-red-400">Failed to load preview</p>
                      <p className="text-xs text-white/50">{meshPreviewError}</p>
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ---- Error messages ---- */}
      {jobError && (
        <div className="mt-2 text-sm text-destructive">{jobError}</div>
      )}
      {exportError && (
        <div className="mt-2 text-sm text-destructive">{exportError}</div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helper functions
// ---------------------------------------------------------------------------

/**
 * Parse filename from Content-Disposition header
 */
function parseFilename(headers: any, fallback: string): string {
  const disposition = headers['content-disposition'];
  if (disposition) {
    const match = disposition.match(/filename\*?=['"]?([^'";]+)['"]?/);
    if (match && match[1]) {
      return decodeURIComponent(match[1]);
    }
  }
  return fallback;
}

/**
 * Trigger browser download
 */
function triggerDownload(blob: Blob, filename: string) {
  const url = window.URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  window.URL.revokeObjectURL(url);
  document.body.removeChild(a);
}

function formatTimestampForFilename(date: Date): string {
  const pad = (value: number) => String(value).padStart(2, '0');
  return [
    date.getFullYear(),
    pad(date.getMonth() + 1),
    pad(date.getDate()),
  ].join('') + '_' + [
    pad(date.getHours()),
    pad(date.getMinutes()),
    pad(date.getSeconds()),
  ].join('');
}
