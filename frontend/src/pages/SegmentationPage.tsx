import { Button } from '@components/ui/button';

/**
 * SegmentationPage
 *
 * AI-powered segmentation interface. Provides:
 * - Organ and lesion auto-segmentation via MONAI backend
 * - Interactive segmentation refinement
 * - Segmentation mask visualization and editing
 * - Label management and export
 */
export default function SegmentationPage() {
  return (
    <div className="flex h-full flex-col p-6">
      {/* Header */}
      <div className="mb-6">
        <h1 className="text-2xl font-bold text-foreground">Segmentation</h1>
        <p className="text-sm text-muted-foreground">
          AI-powered organ and lesion segmentation using MONAI
        </p>
      </div>

      {/* Placeholder content */}
      <div className="flex flex-1 flex-col items-center justify-center text-center">
        <svg className="mb-4 h-16 w-16 text-muted-foreground" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1}>
          <path d="M12 2L2 7l10 5 10-5-10-5z" />
          <path d="M2 17l10 5 10-5" />
          <path d="M2 12l10 5 10-5" />
        </svg>
        <h3 className="mb-2 text-lg font-medium">AI Segmentation</h3>
        <p className="mb-4 max-w-md text-sm text-muted-foreground">
          Load a DICOM study to enable AI-powered organ and lesion segmentation.
          The MONAI backend provides state-of-the-art medical image segmentation.
        </p>
        <div className="flex gap-2">
          <Button variant="default" disabled>
            Run Auto-Segmentation
          </Button>
          <Button variant="outline" disabled>
            Load Model
          </Button>
        </div>
      </div>
    </div>
  );
}
