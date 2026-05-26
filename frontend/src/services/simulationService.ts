import { api } from './api';
import type { SimulationJob } from '@/types/simulation';

/**
 * Simulation Service
 *
 * Service layer for lesion and organ simulation operations.
 * Interfaces with the backend simulation engine for:
 * - Lesion generation with configurable HU values
 * - Organ simulation with realistic tissue properties
 * - Deformation field computation
 * - NIfTI/DICOM export of simulated data
 */

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
  exportResults: (jobId: string, format: 'dicom' | 'nifti' | 'nrrd') =>
    api.post<Blob>(`/simulation/jobs/${jobId}/export?format=${format}`,
      {}, { responseType: 'blob' }),
};
