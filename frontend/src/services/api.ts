import axios, { type AxiosInstance, type AxiosRequestConfig, type AxiosResponse } from 'axios';
import type { APIResponse } from '@types/index';

/**
 * API Service Layer
 *
 * Centralized HTTP client for all backend communications.
 * Handles request/response interceptors, error normalization,
 * and type-safe API calls for all modules.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || '/api/v1';

class ApiService {
  private client: AxiosInstance;

  constructor() {
    this.client = axios.create({
      baseURL: API_BASE_URL,
      timeout: 30000,
      headers: {
        'Content-Type': 'application/json',
      },
    });

    // Request interceptor for auth and logging
    this.client.interceptors.request.use(
      (config) => {
        // Future: attach auth tokens here
        return config;
      },
      (error) => Promise.reject(error),
    );

    // Response interceptor for error normalization
    this.client.interceptors.response.use(
      (response) => response,
      (error) => {
        const normalized = {
          status: error.response?.status || 0,
          message: error.response?.data?.detail || error.message || 'Unknown error',
          data: error.response?.data || null,
        };
        return Promise.reject(normalized);
      },
    );
  }

  /** GET request with generic response typing */
  async get<T>(url: string, config?: AxiosRequestConfig): Promise<APIResponse<T>> {
    const response: AxiosResponse<APIResponse<T>> = await this.client.get(url, config);
    return response.data;
  }

  /** POST request with generic response typing */
  async post<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<APIResponse<T>> {
    const response: AxiosResponse<APIResponse<T>> = await this.client.post(url, data, config);
    return response.data;
  }

  /** PUT request with generic response typing */
  async put<T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<APIResponse<T>> {
    const response: AxiosResponse<APIResponse<T>> = await this.client.put(url, data, config);
    return response.data;
  }

  /** DELETE request with generic response typing */
  async delete<T>(url: string, config?: AxiosRequestConfig): Promise<APIResponse<T>> {
    const response: AxiosResponse<APIResponse<T>> = await this.client.delete(url, config);
    return response.data;
  }

  /** Upload DICOM files with multipart/form-data */
  async uploadDicom(files: File[], studyId?: string): Promise<APIResponse<any>> {
    const formData = new FormData();
    files.forEach((file) => formData.append('dicom_files', file));
    if (studyId) formData.append('study_id', studyId);

    return this.post('/dicom/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
      timeout: 120000, // 2 min for large studies
    });
  }
}

export const api = new ApiService();
