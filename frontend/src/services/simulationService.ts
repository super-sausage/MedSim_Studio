import { api } from './api';
import type {
  AtlasCaseListResponse,
  CtParamsPreviewRequest,
  CtParamsPreviewResponse,
  Lesion3DPreviewRequest,
  Lesion3DPreviewResponse,
  LesionInPhantomRequest,
  LesionInPhantomPreviewResponse,
  DicomLesion3DPreviewRequest,
  DicomLesion3DPreviewResponse,
  LesionConfig,
  SimulationJob,
} from '@/types/simulation';
import type { AxiosProgressEvent, AxiosResponse } from 'axios';

/**
 * Simulation Service
 *
 * Service layer for lesion and organ simulation operations.
 * Interfaces with the backend simulation engine for:
 * - Lesion generation with configurable HU values
 * - Organ simulation with realistic tissue properties
 * - Deformation field computation
 * - NIfTI/DICOM export of simulated data
 * - CT phantom generation (synthetic upper-body CT)
 *
 * Field name mapping (frontend ↔ backend):
 *   type          ↔ lesion_type
 *   center        ↔ center_x, center_y, center_z  (tuple → separate fields)
 *   radiusMm      ↔ radius_x, radius_y, radius_z  (tuple → separate fields)
 */

// ---------------------------------------------------------------------------
// CT Phantom types
// ---------------------------------------------------------------------------

/** Metadata returned alongside the CT phantom volume */
export interface PhantomMetadata {
  width: number;
  height: number;
  depth: number;
  spacing: [number, number, number]; // (z, y, x) in mm
  windowPresets: Record<string, { windowLevel: number; windowWidth: number }>;
  bodyThresholdHU?: number; // voxels >= this HU belong to the body (≈ -500)
  source?: 'procedural' | 'atlas' | 'dicom';
  caseId?: string;
  studyId?: string;
  seriesId?: string;
  labelMap?: Record<number, string>; // organ index → name (atlas only)
  description: string;

  // ---- Debug / Orientation fields (atlas mode) ----
  originalShape?: [number, number, number];    // [z, y, x] before resampling
  outputShape?: [number, number, number];      // [z, y, x] after resampling
  originalSpacing?: [number, number, number];  // [z, y, x] in mm, NIfTI native
  outputSpacing?: [number, number, number];    // [z, y, x] after resampling
  scanAxis?: string;                           // always "z"
  scanDirection?: string;                      // "head_to_feet" or "feet_to_head"
  flippedZ?: boolean;                          // true if z-axis was flipped
  niftiRz?: number;                            // raw S-component of NIfTI z-axis (debug)
  origin?: [number, number, number];
  direction?: [
    [number, number, number],
    [number, number, number],
    [number, number, number],
  ];
  spatialReference?: string;

  // ---- Label statistics (atlas mode, when labels available) ----
  labelNonzeroCounts?: Record<number, number>; // label_id → voxel count
  sliceLabelPresence?: Record<string, [number, number]>; // organ_name → [z_min, z_max]
  labelSource?: string | null;
  labelModelName?: string | null;
  labelError?: string | null;
  labelsEnabled?: boolean;
  segmentationSeriesId?: string | null;
}

/** Response from GET /simulation/phantom */
export interface PhantomResponse {
  volumeBase64: string;
  labelBase64?: string | null; // optional uint8 label map (atlas only)
  metadata: PhantomMetadata;
}

// ---------------------------------------------------------------------------
// Field name conversion helpers
// ---------------------------------------------------------------------------

/**
 * Convert frontend LesionConfig to backend API format.
 *
 * Frontend uses camelCase with tuples for multi-value fields;
 * backend uses snake_case with individual fields.
 */
function toBackendLesion(lesion: LesionConfig): Record<string, unknown> {
  return {
    lesion_type: lesion.type,
    shape: lesion.shape,
    center_x: lesion.center[0],
    center_y: lesion.center[1],
    center_z: lesion.center[2],
    radius_x: lesion.radiusMm[0],
    radius_y: lesion.radiusMm[1],
    radius_z: lesion.radiusMm[2],
    hu_mean: lesion.huMean,
    hu_std: lesion.huStd,
    margin_sharpness: lesion.marginSharpness,
    calcification_fraction: lesion.calcificationFraction,
    necrosis_fraction: lesion.necrosisFraction,
    spiculation_degree: lesion.spiculationDegree,
  };
}

/**
 * Convert backend-format lesion response back to frontend LesionConfig.
 */
function toFrontendLesion(backend: Record<string, unknown>): LesionConfig {
  return {
    type: (backend.lesion_type as LesionConfig['type']) ?? 'tumor',
    shape: (backend.shape as LesionConfig['shape']) ?? 'spherical',
    center: [
      (backend.center_x as number) ?? 0,
      (backend.center_y as number) ?? 0,
      (backend.center_z as number) ?? 0,
    ],
    radiusMm: [
      (backend.radius_x as number) ?? 10,
      (backend.radius_y as number) ?? 10,
      (backend.radius_z as number) ?? 10,
    ],
    huMean: (backend.hu_mean as number) ?? 40,
    huStd: (backend.hu_std as number) ?? 20,
    marginSharpness: (backend.margin_sharpness as number) ?? 0.8,
    calcificationFraction: (backend.calcification_fraction as number) ?? 0,
    necrosisFraction: (backend.necrosis_fraction as number) ?? 0,
    spiculationDegree: (backend.spiculation_degree as number) ?? 0,
  };
}

/**
 * Convert frontend SimulationJob to backend create-job payload.
 * Handles nested lesion and organ config conversion.
 */
function toBackendJob(config: Partial<SimulationJob>): Record<string, unknown> {
  const payload: Record<string, unknown> = {};

  if (config.studyId !== undefined) payload.study_id = config.studyId;
  if (config.seriesId !== undefined) payload.series_id = config.seriesId;
  if (config.outputFormat !== undefined) payload.output_format = config.outputFormat;
  if (config.lesions !== undefined) {
    payload.lesions = config.lesions.map(toBackendLesion);
  }
  // Organs — send as-is (backend accepts List[dict])
  if (config.organs !== undefined) {
    payload.organs = config.organs.map((o) => ({
      organ_type: o.organType,
      hu_mean: o.huMean,
      hu_std: o.huStd,
      enable_noise: o.enableNoise,
      noise_level: o.noiseLevel,
      enable_enhancement: o.enableEnhancement,
      enhancement_pattern: o.enhancementPattern,
    }));
  }

  return payload;
}

// ---------------------------------------------------------------------------
// Typed backend response shape for preview endpoints
// ---------------------------------------------------------------------------

interface PreviewResponse {
  jobId: string;
  previewData: Record<string, unknown>;
  voxelCount: number;
  huRange: [number, number];
}

/** Response from previewing a lesion on a real DICOM series */
export interface DicomLesionPreviewResponse {
  imageBase64: string;
  sliceIndex: number;
  totalSlices: number;
  lesionCenterVoxel: number[];
  huMin: number;
  huMax: number;
  huMean: number;
  huStd: number;
  voxelCount: number;
  volumeMm3: number;
}

// ---------------------------------------------------------------------------
// Service
// ---------------------------------------------------------------------------

export const simulationService = {
  /** List atlas cases available for CT workspace loading */
  getAtlasCases: () =>
    api.get<AtlasCaseListResponse>('/simulation/atlas-cases'),

  /** Create a new simulation job */
  createJob: (config: Partial<SimulationJob>) =>
    api.post<SimulationJob>('/simulation/jobs', toBackendJob(config)),

  /** Get simulation job status */
  getJobStatus: (jobId: string) =>
    api.get<SimulationJob>(`/simulation/jobs/${jobId}`),

  /** List all simulation jobs for a study */
  getStudyJobs: (studyId: string) =>
    api.get<SimulationJob[]>(`/simulation/jobs?study_id=${studyId}`),

  /** Cancel a running simulation job */
  cancelJob: (jobId: string) =>
    api.post(`/simulation/jobs/${jobId}/cancel`),

  /** Generate a lesion preview (fast, synchronous) */
  previewLesion: (config: LesionConfig) =>
    api.post<PreviewResponse>('/simulation/preview/lesion', toBackendLesion(config)),

  /** Preview a lesion overlaid on a real DICOM series */
  previewLesionOnDicom: (
    seriesId: string,
    lesion: LesionConfig,
    windowCenter = 40,
    windowWidth = 400,
    scanDirection: 'head_to_feet' | 'feet_to_head' = 'head_to_feet',
  ) =>
    api.post<DicomLesionPreviewResponse>('/simulation/preview/lesion-on-dicom', {
      series_id: seriesId,
      scan_direction: scanDirection,
      lesion: toBackendLesion(lesion),
      window_center: windowCenter,
      window_width: windowWidth,
    }),

  /** Generate an organ preview */
  previewOrgan: (config: { organType: string; huMean: number; huStd: number; enableNoise?: boolean; noiseLevel?: number; enableEnhancement?: boolean; enhancementPattern?: string }) =>
    api.post<PreviewResponse>('/simulation/preview/organ', {
      organ_type: config.organType,
      hu_mean: config.huMean,
      hu_std: config.huStd,
      enable_noise: config.enableNoise ?? true,
      noise_level: config.noiseLevel ?? 0.1,
      enable_enhancement: config.enableEnhancement ?? false,
      enhancement_pattern: config.enhancementPattern ?? 'none',
    }),

  /** Export simulation results */
  exportResults: (
    jobId: string,
    format: 'dicom' | 'nifti' | 'nrrd',
    onDownloadProgress?: (progressEvent: AxiosProgressEvent) => void,
  ): Promise<AxiosResponse<Blob>> =>
    api.download(`/simulation/jobs/${jobId}/export?format=${format}`, {
      onDownloadProgress,
    }),

  /**
   * Generate a CT phantom — either procedural (geometric) or atlas-based
   * (real CT from disk). Returns base64-encoded Float32 volume + optional
   * uint8 label map + metadata.
   *
   * @param source        'procedural' (default) or 'atlas'
   * @param size          Volume max edge size in voxels (64–320, default 192)
   * @param caseId        Atlas case ID, e.g. 'LUNG1-001' (only used with source='atlas')
   * @param scanDirection Z-axis scan direction: 'head_to_feet' or 'feet_to_head'
   */
  getPhantom: (
    source: 'procedural' | 'atlas' | 'dicom' = 'procedural',
    size = 192,
    caseId = 'LUNG1-001',
    scanDirection: 'head_to_feet' | 'feet_to_head' = 'head_to_feet',
    studyId?: string | null,
    seriesId?: string | null,
    includeLabels = true,
  ): Promise<PhantomResponse> =>
    api.get<PhantomResponse>('/simulation/phantom', {
      timeout: 180000,
      params: {
        source,
        size,
        case_id: caseId,
        scan_direction: scanDirection,
        include_labels: includeLabels,
        ...(studyId ? { study_id: studyId } : {}),
        ...(seriesId ? { series_id: seriesId } : {}),
      },
    }),

  /** Run CT scan parameter simulation preview for the current phantom */
  runCtParamsPreview: (request: CtParamsPreviewRequest): Promise<CtParamsPreviewResponse> =>
    api.post<CtParamsPreviewResponse>('/simulation/ct-params/preview', request, {
      timeout: 180000,
    }),

  /**
   * Preview a lesion as a 3D triangle mesh.
   *
   * The backend generates the lesion volume, runs Marching Cubes,
   * and returns vertices/faces/normals for direct vtk.js rendering.
   *
   * @param lesion  Lesion parameters (same form fields used in 2D preview)
   * @returns       Mesh geometry (vertices, faces, normals, bounds, center, volumeMm3)
   */
  previewLesion3D: (lesion: Lesion3DPreviewRequest): Promise<Lesion3DPreviewResponse> =>
    api.post<Lesion3DPreviewResponse>('/simulation/preview/lesion-3d', lesion, {
      timeout: 60000,
    }),

  /**
   * Generate a lesion embedded inside a CT phantom body for 3D preview.
   *
   * The backend:
   *   1. Generates a procedural upper-body CT phantom (lungs, bones, organs)
   *   2. Places the lesion inside (auto-placed in the right lung if center=0)
   *   3. Bakes the lesion HU values into the phantom volume
   *   4. Extracts the lesion mesh via Marching Cubes
   *   5. Returns both the phantom volume (base64) and the lesion mesh
   *
   * The mesh vertices are pre-offset to align with VTK's centered volume
   * origin, so the frontend can pass them directly to VolumeRenderer
   * as mode='synthetic' syntheticData + lesionMeshes without any transform.
   *
   * @param params  Lesion parameters + phantom configuration
   * @returns       Phantom volume + lesion mesh in aligned coordinate space
   */
  previewLesionInPhantom: (
    params: LesionInPhantomRequest,
  ): Promise<LesionInPhantomPreviewResponse> =>
    api.post<LesionInPhantomPreviewResponse>('/simulation/preview/lesion-in-phantom', params, {
      timeout: 120000,
    }),

  /**
   * Generate a 3D preview of a lesion embedded inside a real DICOM volume.
   *
   * The backend loads the DICOM series, downsamples to a manageable size,
   * generates the lesion at the specified normalized position, bakes it
   * into the volume, and returns both the volume and the lesion mesh.
   *
   * The mesh vertices are pre-offset to align with VTK's centered volume
   * origin for direct overlay in VolumeRenderer mode='synthetic'.
   *
   * @param params  Lesion parameters with seriesId + normalized center
   * @returns       Downsampled DICOM volume + lesion mesh
   */
  previewLesionOnDicom3D: (
    params: DicomLesion3DPreviewRequest,
  ): Promise<DicomLesion3DPreviewResponse> =>
    api.post<DicomLesion3DPreviewResponse>('/simulation/preview/lesion-on-dicom-3d', params, {
      timeout: 180000,
    }),
};

export type { PreviewResponse };
