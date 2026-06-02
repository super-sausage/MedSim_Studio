import { useRef, useEffect, useState, useCallback } from 'react';

// ---------------------------------------------------------------------------
// vtk.js imports
// Profile must load first to register GPU volume ray cast mapper backends
// ---------------------------------------------------------------------------
import '@kitware/vtk.js/Rendering/Profiles/Volume';

import vtkRenderWindow from '@kitware/vtk.js/Rendering/Core/RenderWindow';
import vtkRenderer from '@kitware/vtk.js/Rendering/Core/Renderer';
import vtkOpenGLRenderWindow from '@kitware/vtk.js/Rendering/OpenGL/RenderWindow';
import vtkRenderWindowInteractor from '@kitware/vtk.js/Rendering/Core/RenderWindowInteractor';
import vtkInteractorStyleTrackballCamera from '@kitware/vtk.js/Interaction/Style/InteractorStyleTrackballCamera';
import vtkVolume from '@kitware/vtk.js/Rendering/Core/Volume';
import vtkVolumeMapper from '@kitware/vtk.js/Rendering/Core/VolumeMapper';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkPiecewiseFunction from '@kitware/vtk.js/Common/DataModel/PiecewiseFunction';
import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PresetName = 'ct-bone' | 'ct-soft-tissue' | 'ct-lung' | 'ct-angio';

interface VolumeRendererProps {
  /** Volume data URL or array buffer (unused in Phase 1 — synthetic data) */
  volumeId?: string;
  /** Initial opacity function preset */
  opacityPreset?: PresetName;
  /** Whether to show rendering controls */
  showControls?: boolean;
}

/** Shape of a transfer function preset */
interface TransferFunctionPreset {
  /** [scalarValue, red, green, blue] — 0..1 range */
  colorPoints: Array<[number, number, number, number]>;
  /** [scalarValue, opacity] — 0..1 range */
  opacityPoints: Array<[number, number]>;
}

// ---------------------------------------------------------------------------
// Transfer function presets (scalar range mapped to -1024..3071 HU)
// ---------------------------------------------------------------------------

const PRESETS: Record<PresetName, TransferFunctionPreset> = {
  'ct-bone': {
    colorPoints: [
      [-1024, 0.0, 0.0, 0.0],
      [-400, 0.2, 0.15, 0.1],
      [200, 0.5, 0.35, 0.2],
      [1000, 0.95, 0.95, 0.95],
      [3071, 1.0, 1.0, 1.0],
    ],
    opacityPoints: [
      [-1024, 0.0],
      [-200, 0.0],
      [100, 0.05],
      [500, 0.35],
      [1000, 0.8],
      [3071, 1.0],
    ],
  },

  'ct-soft-tissue': {
    colorPoints: [
      [-1024, 0.0, 0.0, 0.0],
      [-200, 0.1, 0.1, 0.1],
      [40, 0.4, 0.3, 0.2],
      [80, 0.6, 0.5, 0.4],
      [300, 0.9, 0.9, 0.85],
      [3071, 1.0, 1.0, 1.0],
    ],
    opacityPoints: [
      [-1024, 0.0],
      [-200, 0.0],
      [-50, 0.03],
      [40, 0.15],
      [80, 0.4],
      [300, 0.8],
      [3071, 1.0],
    ],
  },

  'ct-lung': {
    colorPoints: [
      [-1024, 0.0, 0.0, 0.0],
      [-900, 0.1, 0.1, 0.1],
      [-700, 0.3, 0.3, 0.3],
      [-500, 0.5, 0.5, 0.5],
      [0, 0.8, 0.75, 0.7],
      [3071, 1.0, 1.0, 1.0],
    ],
    opacityPoints: [
      [-1024, 0.0],
      [-900, 0.05],
      [-750, 0.15],
      [-500, 0.4],
      [0, 0.8],
      [3071, 1.0],
    ],
  },

  'ct-angio': {
    colorPoints: [
      [-1024, 0.0, 0.0, 0.0],
      [-200, 0.1, 0.1, 0.1],
      [50, 0.6, 0.2, 0.15],
      [200, 0.9, 0.3, 0.2],
      [500, 1.0, 0.7, 0.5],
      [3071, 1.0, 1.0, 1.0],
    ],
    opacityPoints: [
      [-1024, 0.0],
      [-50, 0.0],
      [50, 0.05],
      [150, 0.35],
      [300, 0.7],
      [500, 0.95],
      [3071, 1.0],
    ],
  },
};

// ---------------------------------------------------------------------------
// Synthetic volume generation — CT-like phantom (64 × 64 × 64)
// ---------------------------------------------------------------------------

/** Generate a 64³ Float32Array with air (-1000), soft tissue (~40),
 *  bone (~700), and a small calcification (~300).
 *  A bit of sinusoidal texture is added to the soft-tissue region so the
 *  volume does not look perfectly uniform. */
function generateSyntheticVolume(): Float32Array {
  const DIM = 64;
  const size = DIM * DIM * DIM;
  const data = new Float32Array(size);
  const center = DIM / 2; // 32

  for (let z = 0; z < DIM; z++) {
    for (let y = 0; y < DIM; y++) {
      for (let x = 0; x < DIM; x++) {
        const idx = z * DIM * DIM + y * DIM + x;

        const dx = x - center;
        const dy = y - center;
        const dz = z - center;
        const dist = Math.sqrt(dx * dx + dy * dy + dz * dz);

        // ---- air background ----
        let hu = -1000;

        // ---- soft-tissue sphere (r ≈ 22) ----
        if (dist < 22) {
          // Add subtle sinusoidal texture to break uniformity
          const texture =
            Math.sin(dx * 0.3) * Math.cos(dy * 0.3) * Math.sin(dz * 0.3) * 12;
          hu = 40 + texture;
        }

        // ---- bone-like core (r ≈ 8, slightly off-centre) ----
        const bDx = x - (center - 4);
        const bDy = y - (center + 2);
        const bDz = z - (center - 3);
        const boneDist = Math.sqrt(bDx * bDx + bDy * bDy + bDz * bDz);
        if (boneDist < 8) {
          hu = 700 + (Math.random() - 0.5) * 40;
        }

        // ---- small calcification (r ≈ 3) ----
        const cDx = x - (center + 12);
        const cDy = y - (center - 8);
        const cDz = z - (center + 6);
        const calcDist = Math.sqrt(cDx * cDx + cDy * cDy + cDz * cDz);
        if (calcDist < 3) {
          hu = 300;
        }

        data[idx] = hu;
      }
    }
  }

  return data;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VolumeRenderer({
  volumeId: _volumeId,
  opacityPreset: initialPreset = 'ct-soft-tissue',
  showControls = false,
}: VolumeRendererProps) {
  // ---- DOM ref ----
  const containerRef = useRef<HTMLDivElement>(null);

  // ---- VTK object handles (stored in ref to avoid re-render storms) ----
  const vtkRef = useRef<{
    renderWindow: any;
    renderer: any;
    openglRW: any;
    interactor: any;
    volume: any;
    mapper: any;
    imageData: any;
    colorTransfer: any;
    piecewiseFunc: any;
  } | null>(null);

  // ---- UI state ----
  const [isReady, setIsReady] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [activePreset, setActivePreset] = useState<PresetName>(initialPreset);

  // ------------------------------------------------------------------
  // 1. Initialise vtk.js pipeline (runs once per mount)
  // ------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cancelled = false;

    const init = () => {
      setIsLoading(true);

      try {
        // ------- synthetic volume data -------
        const scalarData = generateSyntheticVolume();

        // ------- vtkImageData -------
        const imageData = vtkImageData.newInstance();
        imageData.setDimensions(64, 64, 64);
        imageData.setSpacing([1, 1, 1]);
        imageData.setOrigin([-32, -32, -32]); // centre at world origin

        const dataArray = vtkDataArray.newInstance({
          name: 'Scalars',
          values: scalarData,
          numberOfComponents: 1,
        });
        imageData.getPointData().setScalars(dataArray);

        // ------- rendering pipeline -------
        const renderWindow = vtkRenderWindow.newInstance();
        const renderer = vtkRenderer.newInstance();
        renderer.setBackground(0.0, 0.0, 0.0);
        renderWindow.addRenderer(renderer);

        const openglRW = vtkOpenGLRenderWindow.newInstance();
        openglRW.setContainer(container);
        const { width, height } = container.getBoundingClientRect();
        openglRW.setSize(Math.max(width, 1), Math.max(height, 1));
        renderWindow.addView(openglRW);

        // ------- volume mapper -------
        const mapper = vtkVolumeMapper.newInstance();
        mapper.setInputData(imageData);

        // ------- volume actor -------
        const volume = vtkVolume.newInstance();
        volume.setMapper(mapper);

        // ------- transfer functions (initial preset) -------
        const colorTransfer = vtkColorTransferFunction.newInstance();
        const piecewiseFunc = vtkPiecewiseFunction.newInstance();
        applyPreset(colorTransfer, piecewiseFunc, PRESETS[activePreset]);

        const property = volume.getProperty();
        property.setRGBTransferFunction(0, colorTransfer);
        property.setScalarOpacity(0, piecewiseFunc);
        property.setInterpolationTypeToLinear();
        property.setShade(true);
        property.setAmbient(0.1);
        property.setDiffuse(0.7);
        property.setSpecular(0.2);
        property.setSpecularPower(10.0);

        // ---- independent components (sample distance) ----
        property.setIndependentComponents(true);
        property.setUseGradientOpacity(0, false);

        renderer.addVolume(volume);
        renderer.resetCamera();

        // ------- mouse interaction -------
        const interactor = vtkRenderWindowInteractor.newInstance();
        interactor.setView(openglRW);
        interactor.initialize();
        interactor.setInteractorStyle(
          vtkInteractorStyleTrackballCamera.newInstance(),
        );
        interactor.bindEvents(container);

        // ------- first render -------
        renderWindow.render();

        // ------- store handles for cleanup / preset updates -------
        vtkRef.current = {
          renderWindow,
          renderer,
          openglRW,
          interactor,
          volume,
          mapper,
          imageData,
          colorTransfer,
          piecewiseFunc,
        };

        if (!cancelled) {
          setIsReady(true);
          setIsLoading(false);
        }
      } catch (error) {
        console.error('[VolumeRenderer] Initialization failed:', error);
        if (!cancelled) setIsLoading(false);
      }
    };

    // Defer to next microtask so the container has been laid out
    const timer = setTimeout(init, 0);

    // ------- cleanup -------
    return () => {
      cancelled = true;
      clearTimeout(timer);

      const s = vtkRef.current;
      if (s) {
        try {
          s.interactor.unbindEvents(container);
        } catch (_) {
          /* ignore */
        }
        try {
          s.openglRW.setContainer(null);
        } catch (_) {
          /* ignore */
        }
        try {
          s.renderer.removeVolume(s.volume);
        } catch (_) {
          /* ignore */
        }
        try {
          s.volume.delete();
        } catch (_) {
          /* ignore */
        }
        try {
          s.mapper.delete();
        } catch (_) {
          /* ignore */
        }
        try {
          s.openglRW.delete();
        } catch (_) {
          /* ignore */
        }
        try {
          s.renderer.delete();
        } catch (_) {
          /* ignore */
        }
        try {
          s.renderWindow.delete();
        } catch (_) {
          /* ignore */
        }
        vtkRef.current = null;
      }
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------------------
  // 2. Handle preset changes (update transfer functions in-place)
  // ------------------------------------------------------------------
  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady) return;

    const newColor = vtkColorTransferFunction.newInstance();
    const newOpacity = vtkPiecewiseFunction.newInstance();
    applyPreset(newColor, newOpacity, PRESETS[activePreset]);

    state.volume.getProperty().setRGBTransferFunction(0, newColor);
    state.volume.getProperty().setScalarOpacity(0, newOpacity);

    // Replace stored refs so we don't leak old TF objects
    state.colorTransfer = newColor;
    state.piecewiseFunc = newOpacity;

    state.renderWindow.render();
  }, [activePreset, isReady]);

  // ------------------------------------------------------------------
  // 3. Handle container resize
  // ------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    const state = vtkRef.current;
    if (!container || !state || !isReady) return;

    const observer = new ResizeObserver(() => {
      const { width, height } = container.getBoundingClientRect();
      if (width > 0 && height > 0) {
        state.openglRW.setSize(width, height);
        state.renderWindow.render();
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, [isReady]);

  // ------------------------------------------------------------------
  // 4. Preset button handler
  // ------------------------------------------------------------------
  const handlePresetChange = useCallback((name: PresetName) => {
    setActivePreset(name);
  }, []);

  // ------------------------------------------------------------------
  // Render
  // ------------------------------------------------------------------
  return (
    <div
      className="relative h-full w-full overflow-hidden bg-black"
      onContextMenu={(event) => event.preventDefault()}
    >
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
            <span className="text-xs text-muted-foreground">
              Loading volume...
            </span>
          </div>
        </div>
      )}

      {/* vtk.js container */}
      <div
        ref={containerRef}
        className="h-full w-full"
        style={{ opacity: isReady ? 1 : 0 }}
      />

      {/* Controls overlay */}
      {showControls && isReady && (
        <div className="absolute bottom-2 right-2 z-10 flex gap-1">
          <PresetButton
            label="Bone"
            active={activePreset === 'ct-bone'}
            onClick={() => handlePresetChange('ct-bone')}
          />
          <PresetButton
            label="Soft"
            active={activePreset === 'ct-soft-tissue'}
            onClick={() => handlePresetChange('ct-soft-tissue')}
          />
          <PresetButton
            label="Lung"
            active={activePreset === 'ct-lung'}
            onClick={() => handlePresetChange('ct-lung')}
          />
          <PresetButton
            label="Angio"
            active={activePreset === 'ct-angio'}
            onClick={() => handlePresetChange('ct-angio')}
          />
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Apply a transfer-function preset to brand-new TF instances. */
function applyPreset(
  colorTF: any,
  opacityTF: any,
  preset: TransferFunctionPreset,
): void {
  for (const [x, r, g, b] of preset.colorPoints) {
    colorTF.addRGBPoint(x, r, g, b);
  }
  for (const [x, y] of preset.opacityPoints) {
    opacityTF.addPoint(x, y);
  }
}

/** Small internal button for the preset bar. */
function PresetButton({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded px-2 py-1 text-xs transition-colors ${
        active
          ? 'bg-primary text-primary-foreground'
          : 'bg-black/60 text-white/80 hover:bg-black/80'
      }`}
    >
      {label}
    </button>
  );
}
