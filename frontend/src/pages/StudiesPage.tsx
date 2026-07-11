import { useState, useCallback, useRef, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { Button } from '@components/ui/button';
import { dicomService } from '@services/index';
import type { DicomStudy } from '@/types/index';

/**
 * StudiesPage
 *
 * DICOM study management page. Provides:
 * - Study list with explicit actions
 * - DICOM file upload
 * - Direct handoff into viewer or simulation
 */
export default function StudiesPage() {
  const navigate = useNavigate();
  const [studies, setStudies] = useState<DicomStudy[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isUploading, setIsUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const dirInputRef = useRef<HTMLInputElement>(null);

  const loadStudies = useCallback(async () => {
    setIsLoading(true);
    try {
      const response = await dicomService.getStudies(1, 50);
      setStudies(response.items);
    } catch (error: any) {
      console.error('Failed to load studies:', error);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    loadStudies();
  }, [loadStudies]);

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files || files.length === 0) return;
    setIsUploading(true);
    setUploadError(null);
    try {
      const fileArray = Array.from(files);
      const result = await dicomService.uploadDicom(fileArray);
      await loadStudies();
      navigate(dicomService.buildSimulationPath(result.studyId, null, true));
    } catch (error: any) {
      const message = error?.message || 'Upload failed';
      setUploadError(message);
      console.error('Upload failed:', error);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
      if (dirInputRef.current) {
        dirInputRef.current.value = '';
      }
    }
  }, [loadStudies, navigate]);

  const handleDeleteStudy = useCallback(async (studyId: string) => {
    if (!window.confirm('Delete this study and all its files?')) return;
    setDeletingId(studyId);
    try {
      await dicomService.deleteStudy(studyId);
      await loadStudies();
    } catch (error: any) {
      console.error('Failed to delete study:', error);
    } finally {
      setDeletingId(null);
    }
  }, [loadStudies]);

  return (
    <div className="flex h-full flex-col p-6">
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Studies</h1>
          <p className="text-sm text-muted-foreground">Manage DICOM studies and series</p>
        </div>
        <div className="flex gap-2">
          <input
            ref={fileInputRef}
            type="file"
            className="hidden"
            multiple
            accept=".dcm,.DCM,application/dicom"
            onChange={(e) => handleFileUpload(e.target.files)}
          />
          <input
            ref={dirInputRef}
            type="file"
            className="hidden"
            // @ts-expect-error webkitdirectory is supported by target browsers.
            webkitdirectory=""
            onChange={(e) => handleFileUpload(e.target.files)}
          />
          <Button
            variant="default"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
          >
            {isUploading ? 'Uploading...' : 'Upload DICOM'}
          </Button>
          <Button
            variant="outline"
            onClick={() => dirInputRef.current?.click()}
            disabled={isUploading}
          >
            Upload Folder
          </Button>
        </div>
      </div>

      {uploadError && (
        <div className="mb-4 rounded border border-red-400/30 bg-red-500/10 px-4 py-2 text-sm text-red-400">
          {uploadError}
        </div>
      )}

      <div className="flex-1">
        {isLoading ? (
          <div className="flex h-full items-center justify-center">
            <div className="flex flex-col items-center gap-2">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
              <span className="text-sm text-muted-foreground">Loading studies...</span>
            </div>
          </div>
        ) : studies.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <svg className="mb-4 h-16 w-16 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
              <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
            </svg>
            <h3 className="mb-2 text-lg font-medium">No studies loaded</h3>
            <p className="mb-4 max-w-md text-sm text-muted-foreground">
              Upload DICOM files to begin viewing and analyzing CT images.
            </p>
          </div>
        ) : (
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {studies.map((study) => (
              <div
                key={study.id}
                className="rounded-lg border border-border bg-card transition-colors hover:border-primary/50 hover:bg-accent"
              >
                <button
                  onClick={() => navigate(`/viewer/${study.id}`)}
                  className="w-full p-4 pb-3 text-left"
                >
                  <div className="mb-2 flex items-start justify-between gap-3">
                    <span className="font-medium text-foreground">
                      {study.patientName}
                    </span>
                    <span className="whitespace-nowrap rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">
                      {study.modalities?.join(', ') || 'CT'}
                    </span>
                  </div>
                  <div className="space-y-1 text-xs text-muted-foreground">
                    <p>Patient ID: {study.patientId}</p>
                    {study.studyDescription && (
                      <p className="truncate">{study.studyDescription}</p>
                    )}
                    <p>{study.seriesCount} series, {study.instanceCount} instances</p>
                    {study.studyDate && (
                      <p>{study.studyDate}</p>
                    )}
                  </div>
                </button>

                <div className="flex items-center gap-2 border-t border-border/70 px-4 py-3">
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => navigate(`/viewer/${study.id}`)}
                  >
                    Open Viewer
                  </Button>
                  <Button
                    variant="outline"
                    size="sm"
                    className="flex-1"
                    onClick={() => navigate(dicomService.buildSimulationPath(study.id))}
                  >
                    Open Simulation
                  </Button>
                  <Button
                    variant="destructive"
                    size="sm"
                    onClick={() => handleDeleteStudy(study.id)}
                    disabled={deletingId === study.id}
                  >
                    {deletingId === study.id ? 'Removing...' : 'Remove'}
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
