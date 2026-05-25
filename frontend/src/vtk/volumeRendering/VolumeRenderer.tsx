import { useRef, useEffect, useState } from 'react';

/**
 * VolumeRenderer Component
 *
 * 3D volume rendering viewport using vtk.js.
 * Provides interactive volume visualization with:
 * - Color transfer function presets for CT
 * - Piecewise function opacity control
 * - Interactive camera controls (rotate, pan, zoom)
 * - Volume clipping planes
 * - Region of interest cropping
 */

interface VolumeRendererProps {
  /** Volume data URL or array buffer */
  volumeId?: string;
  /** Initial opacity function preset */
  opacityPreset?: 'ct-bone' | 'ct-soft-tissue' | 'ct-lung' | 'ct-angio';
  /** Whether to show rendering controls */
  showControls?: boolean;
}

export function VolumeRenderer({
  volumeId,
  opacityPreset = 'ct-soft-tissue',
  showControls = false,
}: VolumeRendererProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [isReady, setIsReady] = useState(false);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const initVolumeRenderer = async () => {
      try {
        setIsLoading(true);

        // TODO: Initialize vtk.js full-screen renderer
        // const renderWindow = vtkRenderWindow.newInstance();
        // const renderer = vtkRenderer.newInstance();
        // renderWindow.addRenderer(renderer);
        //
        // // Create OpenGL render window
        // const openglRenderWindow = vtkOpenGLRenderWindow.newInstance();
        // openglRenderWindow.setContainer(container);
        // renderWindow.addView(openglRenderWindow);
        //
        // // Volume mapper and actor
        // const volumeMapper = vtkVolumeMapper.newInstance();
        // const volume = vtkVolume.newInstance();
        // volume.setMapper(volumeMapper);
        //
        // // Color transfer function (CT presets)
        // const colorTransfer = vtkColorTransferFunction.newInstance();
        // // CT bone preset
        // colorTransfer.addRGBPoint(0, 0.0, 0.0, 0.0);
        // colorTransfer.addRGBPoint(200, 0.5, 0.3, 0.2);
        // colorTransfer.addRGBPoint(1000, 1.0, 1.0, 1.0);
        // colorTransfer.addRGBPoint(3000, 1.0, 1.0, 1.0);
        //
        // // Piecewise function for opacity
        // const piecewiseFunction = vtkPiecewiseFunction.newInstance();
        // piecewiseFunction.addPoint(0, 0.0);
        // piecewiseFunction.addPoint(100, 0.05);
        // piecewiseFunction.addPoint(500, 0.3);
        // piecewiseFunction.addPoint(1000, 0.8);
        // piecewiseFunction.addPoint(3000, 1.0);
        //
        // volume.getProperty().setRGBTransferFunction(0, colorTransfer);
        // volume.getProperty().setScalarOpacity(0, piecewiseFunction);
        // volume.getProperty().setInterpolationTypeToLinear();
        // volume.getProperty().setShade(true);
        //
        // renderer.addVolume(volume);
        // renderer.resetCamera();
        // renderWindow.render();

        setIsReady(true);
        setIsLoading(false);
      } catch (error) {
        console.error('[VolumeRenderer] Init failed:', error);
        setIsLoading(false);
      }
    };

    initVolumeRenderer();

    return () => {
      // Cleanup vtk.js resources
      // openglRenderWindow?.setContainer(null);
      // renderWindow?.delete();
    };
  }, [volumeId, opacityPreset]);

  return (
    <div className="relative h-full w-full overflow-hidden bg-black">
      {/* Orientation label */}
      <div className="pointer-events-none absolute left-2 top-2 z-10">
        <span className="rounded bg-black/60 px-2 py-0.5 text-xs font-medium uppercase text-white">
          3D Volume
        </span>
      </div>

      {/* Loading indicator */}
      {isLoading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/50">
          <div className="flex flex-col items-center gap-2">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-primary border-t-transparent" />
            <span className="text-xs text-muted-foreground">Loading volume...</span>
          </div>
        </div>
      )}

      {/* vtk.js container */}
      <div ref={containerRef} className="h-full w-full" style={{ opacity: isReady ? 1 : 0 }} />

      {/* Controls overlay */}
      {showControls && isReady && (
        <div className="absolute bottom-2 right-2 z-10 flex gap-1">
          <PresetButton label="Bone" active={opacityPreset === 'ct-bone'} />
          <PresetButton label="Soft" active={opacityPreset === 'ct-soft-tissue'} />
          <PresetButton label="Lung" active={opacityPreset === 'ct-lung'} />
        </div>
      )}
    </div>
  );
}

function PresetButton({ label, active }: { label: string; active: boolean }) {
  return (
    <button
      className={`rounded px-2 py-1 text-xs ${
        active ? 'bg-primary text-primary-foreground' : 'bg-black/60 text-white/80 hover:bg-black/80'
      }`}
    >
      {label}
    </button>
  );
}
