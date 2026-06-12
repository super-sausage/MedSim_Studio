import { api } from './api';
import type { SimulationJob } from '@/types/simulation';
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
 */

/** Metadata returned alongside the CT phantom volume */
export interface PhantomMetadata {
  width: number;
  height: number;
  depth: number;
  spacing: [number, number, number]; // (z, y, x) in mm
  windowPresets: Record<string, { windowLevel: number; windowWidth: number }>;
  bodyThresholdHU?: number; // voxels >= this HU belong to the body (≈ -500)
  source?: 'procedural' | 'atlas';
  caseId?: string;
  labelMap?: Record<number, string>; // organ index → name (atlas only)
  description: string;

  // ---- Debug / orientation fields (atlas mode) ----
  originalShape?: [number, number, number];    // [z, y, x] before resampling
  outputShape?: [number, number, number];      // [z, y, x] after resampling
  originalSpacing?: [number, number, number];  // [z, y, x] in mm, NIfTI native
  outputSpacing?: [number, number, number];    // [z, y, x] after resampling
  scanAxis?: string;                           // always "z"
  scanDirection?: string;                      // "head_to_feet" or "feet_to_head"
  flippedZ?: boolean;                          // true if z-axis was flipped
  niftiRz?: number;                            // raw S-component of NIfTI z-axis (debug)

  // ---- Label statistics (atlas mode, when labels available) ----
  labelNonzeroCounts?: Record<number, number>; // label_id → voxel count
  sliceLabelPresence?: Record<string, [number, number]>; // organ_name → [z_min, z_max]
}

/** Response from GET /simulation/phantom */
export interface PhantomResponse {
  volumeBase64: string;
  labelBase64?: string | null; // optional uint8 label map (atlas only)
  metadata: PhantomMetadata;
}

export const simulationService = {
  /** Create a new simulation job */
  createJob: (config: Partial<SimulationJob>) =>
    api.post<SimulationJob>('/simulation/jobs', config),

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
  previewLesion: (config: any) =>
    api.post<any>('/simulation/preview/lesion', config),

  /** Generate an organ preview */
  previewOrgan: (config: any) =>
    api.post<any>('/simulation/preview/organ', config),

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
   * @param caseId        Atlas case ID, e.g. 's0001' (only used with source='atlas')
   * @param scanDirection Z-axis scan direction: 'head_to_feet' or 'feet_to_head'
   */
  getPhantom: (
    source: 'procedural' | 'atlas' = 'procedural',
    size = 192,
    caseId = 's0001',
    scanDirection: 'head_to_feet' | 'feet_to_head' = 'head_to_feet',
  ): Promise<PhantomResponse> =>
    api.get<PhantomResponse>(
      `/simulation/phantom?source=${source}&size=${size}&case_id=${caseId}&scan_direction=${scanDirection}`,
    ),
};
