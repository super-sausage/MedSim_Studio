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
  maskVolume: number; // cm³
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
