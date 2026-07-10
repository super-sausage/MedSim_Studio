/**
 * Simulation Type Definitions
 *
 * Types for lesion/organ simulation and Hounsfield Unit (HU)
 * manipulation within the CT Simulator platform.
 */

/** Lesion types available for simulation */
export type LesionType = 'tumor' | 'nodule' | 'cyst' | 'calcification' | 'metastasis';

/** Geometric shape for simulated lesions */
export type LesionShape = 'spherical' | 'ellipsoidal' | 'irregular' | 'lobulated' | 'spiculated';

/** Lesion parameters for simulation generation */
export interface LesionConfig {
  type: LesionType;
  shape: LesionShape;
  center: [number, number, number]; // voxel coordinates
  radiusMm: [number, number, number]; // radii in mm (x, y, z)
  huMean: number; // mean Hounsfield Unit value
  huStd: number; // HU standard deviation (heterogeneity)
  marginSharpness: number; // 0 = diffuse, 1 = sharp
  calcificationFraction: number; // 0-1, internal calcification
  necrosisFraction: number; // 0-1, internal necrosis
  spiculationDegree: number; // 0-1, spiculation for malignant appearance
}

/** Organ simulation parameters */
export interface OrganConfig {
  organType: OrganType;
  huMean: number;
  huStd: number;
  enableNoise: boolean;
  noiseLevel: number;
  enableEnhancement: boolean;
  enhancementPattern: EnhancementPattern;
}

export type OrganType =
  | 'liver'
  | 'kidney'
  | 'lung'
  | 'brain'
  | 'bone'
  | 'heart'
  | 'spleen'
  | 'pancreas'
  | 'bladder';

export type EnhancementPattern = 'homogeneous' | 'heterogeneous' | 'rim' | 'septal' | 'none';

/** Deformation field parameters */
export interface DeformationConfig {
  deformationType: 'rigid' | 'affine' | 'bspline' | 'demons';
  magnitude: number;
  controlPoints: [number, number, number][];
  smoothingSigma: number;
}

/** HU modifier for adjusting tissue densities */
export interface HUModifier {
  regionId: string;
  originalHU: number;
  modifiedHU: number;
  maskVolume: number; // cm鲁
  operation: 'add' | 'subtract' | 'replace' | 'scale';
}

/** Full simulation job configuration */
export interface SimulationJob {
  id: string;
  studyId: string;
  seriesId: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  lesions: LesionConfig[];
  organs: OrganConfig[];
  deformation: DeformationConfig | null;
  outputFormat: 'dicom' | 'nifti' | 'nrrd';
  createdAt: string;
  progress: number;
}

export type CtDoseLevel = 'low' | 'standard' | 'high';
export type CtKernel = 'smooth' | 'soft' | 'standard' | 'lung' | 'bone' | 'sharp';
export type CtContrastPhase = 'noncontrast' | 'arterial' | 'venous' | 'delayed';
export type CtScanDirection = 'head_to_feet' | 'feet_to_head';
export type CtPhantomSource = 'procedural' | 'atlas' | 'dicom';
export type CtSliceThickness = 0.625 | 1.0 | 2.5 | 5.0 | 10.0 | 15.0 | 20.0;
export type CtKvp = 80 | 100 | 120 | 140;
export type CtPitch = 0.5 | 0.8 | 1.0 | 1.2 | 1.5;
export type CtFovMm = 150 | 250 | 350 | 500;
export type CtMatrixSize = 256 | 512 | 1024;

export interface CtParamsPreviewParams {
  gantryPitchDeg: number;
  gantryYawDeg: number;
  gantryRollDeg: number;
  sliceThicknessMm: CtSliceThickness;
  doseLevel: CtDoseLevel;
  mAs: number;
  kVp: CtKvp;
  pitch: CtPitch;
  fovMm: CtFovMm;
  matrixSize: CtMatrixSize;
  kernel: CtKernel;
  contrastPhase: CtContrastPhase;
}

export interface CtParamsPreviewRequest {
  source: CtPhantomSource;
  caseId?: string | null;
  studyId?: string | null;
  seriesId?: string | null;
  size: number;
  scanDirection: CtScanDirection;
  params: CtParamsPreviewParams;
}

export interface CtCenterSliceStats {
  sliceIndex: number;
  min: number;
  max: number;
  mean: number;
  std: number;
}

export interface CtParamsPreviewMetadata {
  shape: number[];
  spacing: number[];
  huRange: [number, number];
  gantryPitchDeg: number;
  gantryYawDeg: number;
  gantryRollDeg: number;
  effectiveSliceThicknessMm: number;
  algorithmNotes?: string[];
  warnings?: string[];
  source?: CtPhantomSource;
  caseId?: string;
  studyId?: string;
  seriesId?: string;
  spatialReference?: string;
  scanDirection?: CtScanDirection;
  previewStats?: {
    originalCenterSliceStats?: CtCenterSliceStats;
    simulatedCenterSliceStats?: CtCenterSliceStats;
  };
  phantomMetadata?: {
    originalShape?: number[];
    outputShape?: number[];
    originalSpacing?: number[];
    outputSpacing?: number[];
    flippedZ?: boolean;
  };
}

export interface CtParamsJson {
  requestedParams: Record<string, unknown>;
  resolvedParams: Record<string, unknown>;
  algorithmSteps: Array<Record<string, unknown>>;
  approximationNotes?: string[];
  warnings?: string[];
  inputShape?: number[];
  outputShape?: number[];
  inputSpacing?: number[];
  outputSpacing?: number[];
  huRangeBefore?: [number, number];
  huRangeAfter?: [number, number];
}

export interface StandardizedCtCaseVolume {
  encoding: 'base64';
  dtype: 'float32';
  byteOrder: 'little_endian';
  axisOrder: 'zyx';
  shape: [number, number, number];
  spacing: [number, number, number];
  origin: [number, number, number];
  direction: [
    [number, number, number],
    [number, number, number],
    [number, number, number],
  ];
  spatialReference?: string;
  huRange: [number, number];
  sliceCount: number;
  modality: 'CT';
  bodyPart: string;
  imageKind: 'simulated_ct';
  imageDataField: 'simulatedVolumeBase64';
}
export interface StandardizedCtCaseSimulation {
  type: 'ct_scan_params';
  paramsJson: CtParamsJson;
  algorithm: 'image_domain_approximation';
  approximationWarning: string;
}
export interface StandardizedCtCase {
  caseId?: string | null;
  source: CtPhantomSource;
  sourceCaseId: string | null;
  volume: StandardizedCtCaseVolume;
  simulation: StandardizedCtCaseSimulation;
  metadata: CtParamsPreviewMetadata;
}
export interface CtParamsPreviewResponse {
  simulatedVolumeBase64: string;
  simulatedLabelBase64?: string | null;
  metadata: CtParamsPreviewMetadata;
  paramsJson: CtParamsJson;
  standardizedCase: StandardizedCtCase;
}

export interface AtlasCaseOption {
  caseId: string;
  label: string;
}

export interface AtlasCaseListResponse {
  items: AtlasCaseOption[];
  count: number;
}

// ---------------------------------------------------------------------------
// Phase 4/5: 3D Lesion Mesh Preview Types
// ---------------------------------------------------------------------------

/** Request for 3D mesh preview — mirrors backend Lesion3DPreviewRequest
 *  (api.ts interceptor converts camelCase → snake_case for POST) */
export interface Lesion3DPreviewRequest {
  lesionType: LesionType;
  shape: LesionShape;
  centerX: number;
  centerY: number;
  centerZ: number;
  radiusX: number;
  radiusY: number;
  radiusZ: number;
  huMean: number;
  huStd: number;
  marginSharpness: number;
  spiculationDegree: number;
  previewSize?: number;
  spacing?: [number, number, number];
}

/** Triangle mesh data for a single lesion, as returned by the backend */
export interface Lesion3DPreviewResponse {
  vertices: number[][];
  faces: number[][];
  normals: number[][];
  bounds: {
    min: [number, number, number];
    max: [number, number, number];
  };
  center: [number, number, number];
  volumeMm3: number;
}

/** Per-lesion mesh data + rendering options for VolumeRenderer */
export interface LesionMeshData {
  /** Unique ID (used for React key / visibility toggling) */
  id: string;
  /** Triangle vertices in physical (x, y, z) mm (N × 3) */
  vertices: number[][];
  /** Triangle face indices into vertices (M × 3) */
  faces: number[][];
  /** Per-vertex normal vectors (N × 3) */
  normals: number[][];
  /** Opacity 0..1 (default 1) */
  opacity?: number;
  /** RGB color 0..1 (default [1, 0.3, 0.3] ≈ red) */
  color?: [number, number, number];
  /** Visibility (default true) */
  visible?: boolean;
}

// ---------------------------------------------------------------------------
// Lesion-in-Phantom 3D Preview — CT phantom body + embedded lesion mesh
// ---------------------------------------------------------------------------

/** Request parameters for lesion-in-phantom 3D preview.
 *  Mirrors the backend LesionInPhantomPreviewRequest schema. */
export interface LesionInPhantomRequest {
  lesionType: LesionType;
  shape: LesionShape;
  radiusX: number;
  radiusY: number;
  radiusZ: number;
  huMean: number;
  huStd: number;
  marginSharpness: number;
  spiculationDegree: number;
  phantomSize?: number;
  /** Normalized center coordinates (0-1). When all > 0, backend scales to phantom dimensions. */
  normalizedCenterX?: number;
  normalizedCenterY?: number;
  normalizedCenterZ?: number;
  /** Raw voxel center (fallback when normalized coords are not set). */
  centerX?: number;
  centerY?: number;
  centerZ?: number;
}

// ---------------------------------------------------------------------------
// Lesion-in-Phantom 3D Preview — CT phantom body + embedded lesion mesh
// ---------------------------------------------------------------------------

/** Response from lesion-in-phantom preview endpoint.
 *  Contains both the CT phantom volume (for background rendering) and
 *  the lesion mesh (for overlay), pre-aligned in the same coordinate space. */
export interface LesionInPhantomPreviewResponse {
  /** Base64-encoded raw Float32 bytes (little-endian, z/y/x axis order, x fastest) */
  phantomVolumeBase64: string;
  /** Volume shape [z, y, x] in voxels */
  phantomShape: [number, number, number];
  /** Voxel spacing [z, y, x] in mm */
  phantomSpacing: [number, number, number];
  /** N×3 lesion mesh vertices in physical (x, y, z) mm, aligned with VTK centered volume */
  lesionVertices: number[][];
  /** M×3 triangle face indices into vertices */
  lesionFaces: number[][];
  /** N×3 per-vertex normal vectors */
  lesionNormals: number[][];
  /** Lesion bounding-box center [cx, cy, cz] in mm */
  lesionCenterMm: [number, number, number];
  /** Approximate enclosed volume in mm³ */
  lesionVolumeMm3: number;
}

// ---------------------------------------------------------------------------
// DICOM 3D Lesion Preview — lesion embedded in real DICOM volume
// ---------------------------------------------------------------------------

export interface DicomLesion3DPreviewRequest {
  seriesId: string;
  scanDirection?: CtScanDirection;
  lesionType: LesionType;
  shape: LesionShape;
  radiusX: number;
  radiusY: number;
  radiusZ: number;
  huMean: number;
  huStd: number;
  marginSharpness: number;
  spiculationDegree: number;
  normalizedCenterX?: number;
  normalizedCenterY?: number;
  normalizedCenterZ?: number;
  previewSize?: number;
}

export interface DicomLesion3DPreviewResponse {
  volumeBase64: string;
  volumeShape: [number, number, number];
  volumeSpacing: [number, number, number];
  lesionVertices: number[][];
  lesionFaces: number[][];
  lesionNormals: number[][];
  lesionCenterMm: [number, number, number];
  lesionVolumeMm3: number;
}

