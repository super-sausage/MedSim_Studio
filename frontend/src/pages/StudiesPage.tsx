import { useState, useCallback, useRef } from 'react';
import { Button } from '@components/ui/button';

/**
 * StudiesPage
 *
 * DICOM study management page. Provides:
 * - Study list with search and filtering
 * - DICOM file upload
 * - Navigation to the viewer
 */
export default function StudiesPage() {
  const [studies] = useState<any[]>([]);
  const [isUploading, setIsUploading] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileUpload = useCallback(async (files: FileList | null) => {
    if (!files) return;
    setIsUploading(true);
    try {
      // TODO: Implement DICOM upload via dicomService
      console.log(`Uploading ${files.length} DICOM files`);
    } catch (error) {
      console.error('Upload failed:', error);
    } finally {
      setIsUploading(false);
    }
  }, []);

  return (
    <div className="flex h-full flex-col p-6">
      {/* Header */}
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
            accept=".dcm,.DCM"
            onChange={(e) => handleFileUpload(e.target.files)}
          />
          <Button
            variant="default"
            onClick={() => fileInputRef.current?.click()}
            disabled={isUploading}
          >
            {isUploading ? 'Uploading...' : 'Upload DICOM'}
          </Button>
        </div>
      </div>

      {/* Study list */}
      <div className="flex-1">
        {studies.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center text-center">
            <svg className="mb-4 h-16 w-16 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
              <path d="M22 19a2 2 0 01-2 2H4a2 2 0 01-2-2V5a2 2 0 012-2h5l2 3h9a2 2 0 012 2z" />
            </svg>
            <h3 className="mb-2 text-lg font-medium">No studies loaded</h3>
            <p className="mb-4 max-w-md text-sm text-muted-foreground">
              Upload DICOM files to begin viewing and analyzing CT images.
              Drag and drop or use the upload button above.
            </p>
          </div>
        ) : (
          <div className="grid gap-4">
            {/* Study cards would render here */}
          </div>
        )}
      </div>
    </div>
  );
}
