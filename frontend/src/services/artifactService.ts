import { api } from './api';

export interface ArtifactGenerateRequest {
  artifactType: string;
  params: Record<string, unknown>;
  source?: 'phantom' | 'dicom';
  seriesId?: string;
  studyId?: string;
  sliceIndex?: number;
}

export interface ArtifactGenerateResponse {
  artifactType: string;
  originalSlice: number[][];
  artifactSlice: number[][];
  maskSlice: number[][];
  metadata: Record<string, unknown>;
  shape: number[];
  spacing: number[];
  source: string;
}

export interface SeriesInfo {
  id: string;
  studyId: string;
  description: string | null;
  modality: string | null;
  imageCount: number | null;
  rows: number | null;
  columns: number | null;
}

export interface ClassifyRequest {
  source?: 'phantom' | 'dicom';
  seriesId?: string;
  sliceIndices?: number[];
}

export interface SliceClassifyResult {
  scores: Record<string, number>;
  labels: string[];
  dominant: string;
  sliceIndex: number;
}

export interface ClassifyResponse {
  overallScores: Record<string, number>;
  perSliceScores: SliceClassifyResult[];
  dominantArtifact: string;
  sliceCount: number;
}

export interface TrainRequest {
  epochs: number;
  batchSize: number;
  learningRate: number;
  numVolumes: number;
  outputDir: string;
}

export interface TrainEpochResult {
  epoch: number;
  trainLoss: number;
  valLoss: number;
  trainAcc: number;
  valAcc: number;
  trainF1: number;
  valF1: number;
}

export interface TrainStatusResponse {
  status: string;
  currentEpoch: number;
  totalEpochs: number;
  trainLoss: number;
  valLoss: number;
  trainAcc: number;
  valAcc: number;
  trainF1: number;
  valF1: number;
  bestValLoss: number;
  epochHistory: TrainEpochResult[];
  error: string | null;
  startTime: number | null;
}

export interface TrainHistoryResponse {
  epochs: TrainEpochResult[];
  bestValLoss: number;
  outputDir: string;
}

export const artifactService = {
  async getTypes(): Promise<string[]> {
    const res = await api.get<{ types: string[] }>('/artifact/types');
    return res.types;
  },

  async getSeries(studyId?: string): Promise<SeriesInfo[]> {
    const params = studyId ? { study_id: studyId } : {};
    return api.get<SeriesInfo[]>('/artifact/series', { params });
  },

  async generate(req: ArtifactGenerateRequest): Promise<ArtifactGenerateResponse> {
    return api.generateArtifact<ArtifactGenerateResponse>('/artifact/generate', {
      artifact_type: req.artifactType,
      params: req.params,
      source: req.source ?? 'phantom',
      series_id: req.seriesId,
      study_id: req.studyId,
      slice_index: req.sliceIndex,
    });
  },

  async classify(req: ClassifyRequest): Promise<ClassifyResponse> {
    return api.generateArtifact<ClassifyResponse>('/artifact/classify', {
      source: req.source ?? 'phantom',
      series_id: req.seriesId,
      slice_indices: req.sliceIndices,
    });
  },

  async startTraining(req: TrainRequest): Promise<{ message: string; epochs: number; outputDir: string }> {
    return api.post('/artifact/train', {
      epochs: req.epochs,
      batch_size: req.batchSize,
      learning_rate: req.learningRate,
      num_volumes: req.numVolumes,
      output_dir: req.outputDir,
    });
  },

  async getTrainStatus(): Promise<TrainStatusResponse> {
    return api.get('/artifact/train/status');
  },

  async getTrainHistory(): Promise<TrainHistoryResponse> {
    return api.get('/artifact/train/history');
  },
};
