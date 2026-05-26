import { useViewerStore } from '@store/useViewerStore';
import { MPRViewer } from '@viewer/mpr/MPRViewer';
import { VolumeRenderer } from '@vtk/volumeRendering/VolumeRenderer';
import { WINDOW_LEVEL_PRESETS } from '@/types/dicom';
import { Button } from '@components/ui/button';

/**
 * ViewerPage
 *
 * Main medical image viewer page with MPR (multi-planar reconstruction)
 * and 3D volume rendering capabilities. Provides:
 * - Axial, sagittal, and coronal views
 * - 3D volume rendering via vtk.js
 * - Window/level presets
 * - Tool selection for measurements and annotations
 */

const viewportPresets = [
  { id: 'mpr-3d', label: 'MPR + 3D', layout: ['axial', 'sagittal', 'coronal', '3d'] },
  { id: 'mpr-only', label: 'MPR Only', layout: ['axial', 'sagittal', 'coronal'] },
  { id: 'single', label: 'Single', layout: ['axial'] },
];

export default function ViewerPage() {
  const { mprState, activePreset, setWindowLevel, volumeRenderingEnabled, toggleVolumeRendering } =
    useViewerStore();

  return (
    <div className="flex h-full flex-col">
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b border-border bg-card px-4 py-2">
        <div className="flex items-center gap-1">
          {viewportPresets.map((preset) => (
            <Button key={preset.id} variant="ghost" size="sm">
              {preset.label}
            </Button>
          ))}
        </div>

        <div className="mx-2 h-6 w-px bg-border" />

        <div className="flex items-center gap-1">
          {WINDOW_LEVEL_PRESETS.map((preset) => (
            <Button
              key={preset.name}
              variant={activePreset?.name === preset.name ? 'secondary' : 'ghost'}
              size="sm"
              onClick={() => setWindowLevel(preset)}
              title={preset.description}
            >
              {preset.name}
            </Button>
          ))}
        </div>

        <div className="mx-2 h-6 w-px bg-border" />

        <Button
          variant={volumeRenderingEnabled ? 'secondary' : 'ghost'}
          size="sm"
          onClick={toggleVolumeRendering}
        >
          3D
        </Button>
      </div>

      {/* Viewport grid */}
      <div className="flex-1 bg-black">
        {!mprState ? (
          <div className="flex h-full items-center justify-center">
            <div className="text-center">
              <p className="text-lg text-muted-foreground">No study loaded</p>
              <p className="text-sm text-muted-foreground">
                Select a study from the Studies page to begin viewing
              </p>
            </div>
          </div>
        ) : (
          <div className="grid h-full grid-cols-2 grid-rows-2 gap-px bg-border">
            <MPRViewer orientation="axial" />
            <MPRViewer orientation="sagittal" />
            <MPRViewer orientation="coronal" />
            {volumeRenderingEnabled && <VolumeRenderer />}
          </div>
        )}
      </div>

      {/* Status bar */}
      <div className="flex items-center justify-between border-t border-border bg-card px-4 py-1 text-xs text-muted-foreground">
        <span>Patient: --</span>
        <span>Study: --</span>
        <span>Series: --</span>
        <span>WW: {activePreset?.windowWidth ?? '--'} / WC: {activePreset?.windowCenter ?? '--'}</span>
      </div>
    </div>
  );
}
