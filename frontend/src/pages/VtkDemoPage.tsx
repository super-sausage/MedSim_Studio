import { VolumeRenderer } from '@vtk';

/**
 * VtkDemoPage - development sandbox for vtk.js volume rendering.
 *
 * Renders the VolumeRenderer component with a synthetic CT-like phantom
 * so we can validate the vtk.js pipeline before wiring real DICOM data.
 *
 * Route: /vtk-demo
 */
export default function VtkDemoPage() {
  return (
    <div className="flex min-h-screen flex-col bg-black text-white">
      <div className="flex items-center gap-3 border-b border-white/10 bg-black/80 px-4 py-2">
        <span className="text-sm font-medium text-white/80">
          vtk.js Demo — Synthetic CT Phantom
        </span>
        <span className="rounded bg-yellow-500/20 px-2 py-0.5 text-[10px] font-medium uppercase text-yellow-400">
          Dev
        </span>
      </div>

      <div className="relative flex-1 overflow-hidden bg-black">
        <VolumeRenderer showControls opacityPreset="ct-soft-tissue" />
      </div>
    </div>
  );
}
