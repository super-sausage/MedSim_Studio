import { useRef, useEffect, useState, useCallback } from 'react';

// ---------------------------------------------------------------------------
// vtk.js imports
// Profile must load first to register GPU mappers (Volume + Geometry for mesh)
// ---------------------------------------------------------------------------
import '@kitware/vtk.js/Rendering/Profiles/All';

import vtkRenderWindow from '@kitware/vtk.js/Rendering/Core/RenderWindow';
import vtkRenderer from '@kitware/vtk.js/Rendering/Core/Renderer';
import vtkOpenGLRenderWindow from '@kitware/vtk.js/Rendering/OpenGL/RenderWindow';
import vtkRenderWindowInteractor from '@kitware/vtk.js/Rendering/Core/RenderWindowInteractor';
import vtkInteractorStyleTrackballCamera from '@kitware/vtk.js/Interaction/Style/InteractorStyleTrackballCamera';
import vtkVolume from '@kitware/vtk.js/Rendering/Core/Volume';
import vtkVolumeMapper from '@kitware/vtk.js/Rendering/Core/VolumeMapper';
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor';
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper';
import vtkColorTransferFunction from '@kitware/vtk.js/Rendering/Core/ColorTransferFunction';
import vtkPiecewiseFunction from '@kitware/vtk.js/Common/DataModel/PiecewiseFunction';
import vtkImageData from '@kitware/vtk.js/Common/DataModel/ImageData';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkPlane from '@kitware/vtk.js/Common/DataModel/Plane';
import vtkImageMarchingCubes from '@kitware/vtk.js/Filters/General/ImageMarchingCubes';
import vtkWindowedSincPolyDataFilter from '@kitware/vtk.js/Filters/General/WindowedSincPolyDataFilter';
import vtkPolyDataNormals from '@kitware/vtk.js/Filters/Core/PolyDataNormals';

import { loadDicomVolume, type DicomVolumeData } from './dicomVolumeLoader';
import { createLesionActor } from '../mesh/createLesionActor';
import type { LesionActorResult } from '../mesh/createLesionActor';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

type PresetName = 'ct-bone' | 'ct-soft-tissue' | 'ct-lung' | 'ct-angio';

/** Normalised clipping plane positions (0..1). 0 = disabled, 1 = max clip. */
interface ClipState {
  x: number;
  y: number;
  z: number;
}

/** A single segmentation label with RGB color */
interface SegmentationLabelDef {
  index: number;
  name: string;
  color: [number, number, number]; // RGB 0-255
  visible?: boolean;
  selected?: boolean;
  opacity?: number;
}

/** A single lesion mesh for rendering overlay or standalone preview */
interface LesionMeshProp {
  /** Unique ID for React tracking */
  id: string;
  /** N×3 vertex positions in physical (x, y, z) mm */
  vertices: number[][];
  /** M×3 triangle face indices (0-based) */
  faces: number[][];
  /** N×3 per-vertex normal vectors */
  normals: number[][];
  /** Opacity 0..1 (default 1.0) */
  opacity?: number;
  /** RGB color components 0..1 (default [1, 0.3, 0.3]) */
  color?: [number, number, number];
  /** Visibility toggle (default true) */
  visible?: boolean;
}

interface OrganSurfaceRef {
  labelIndex: number;
  actor: any;
  mapper: any;
  imageData: any;
  marchingCubes: any;
  smoother: any;
  normals: any;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DEFAULT_SEG_OPACITY = 0.14;
const SELECTED_OPACITY_BOOST = 0.16;
const MIN_ORGAN_SURFACE_VOXELS = 8;
const SURFACE_SMOOTHING_ITERATIONS = 8;
const SURFACE_SMOOTHING_PASS_BAND = 0.2;

interface VolumeRendererProps {
  /** Render mode: synthetic phantom (default), real DICOM series, or standalone mesh preview */
  mode?: 'synthetic' | 'dicom' | 'mesh';
  /** DICOM series ID — required when mode='dicom' */
  seriesId?: string;
  /** Initial opacity function preset */
  opacityPreset?: PresetName;
  /** Whether to show rendering controls */
  showControls?: boolean;
  /** Optional 3D segmentation label map for overlay */
  segmentationMask?: Float32Array | null;
  /** Label definitions for coloring the segmentation mask */
  segmentationLabels?: SegmentationLabelDef[] | null;
  /**
   * External volume data for synthetic mode.
   * When provided with mode='synthetic', this replaces the built-in
   * 64³ phantom. scalarData must be a Float32Array of HU values
   * in [x, y, z] fast-axis order (x fastest).
   */
  syntheticData?: Float32Array | null;
  /** Dimensions [x, y, z] for syntheticData (required if syntheticData given) */
  syntheticDims?: [number, number, number] | null;
  /** Spacing [x, y, z] in mm for syntheticData */
  syntheticSpacing?: [number, number, number] | null;
  /**
   * Progressive-scan clip index (0-based).
   * When set, only voxels along the scan axis at indices 0..clipIndex
   * are visible; indices beyond are clipped away.  Used to simulate
   * a CT scan that builds up slice-by-slice from top to bottom.
   * Undefined / -1 disables scan clipping (full volume shown).
   */
  syntheticClipIndex?: number;
  syntheticClipDirection?: 'low_to_high' | 'high_to_low';
  /**
   * Axis along which the progressive scan proceeds.
   * 'x', 'y', or 'z' — defaults to 'z' (superior→inferior for CT phantom).
   */
  syntheticScanAxis?: 'x' | 'y' | 'z';
  /**
   * Enable head-to-feet scan view mode.
   * When true, the camera is oriented so the Z axis (head→feet direction)
   * points downward on screen, making the progressive scan appear to build
   * from top (head/chest) to bottom (abdomen/pelvis).
   * Also enables the scan-plane overlay and direction labels.
   */
  scanView?: boolean;
  /**
   * Scan direction label — used for the overlay text.
   * 'head_to_feet' (default) or 'feet_to_head'.
   */
  scanDirection?: 'head_to_feet' | 'feet_to_head';
  /**
   * Lesion meshes to render.
   * In mode='mesh': rendered standalone on dark background.
   * In mode='synthetic'|'dicom': overlaid on the CT volume.
   * Supports multiple lesions for multi-lesion scenarios.
   */
  lesionMeshes?: LesionMeshProp[] | null;
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

function centerVolumeOrigin(
  dimensions: [number, number, number],
  spacing: [number, number, number],
): [number, number, number] {
  return [
    -((dimensions[0] - 1) * spacing[0]) / 2,
    -((dimensions[1] - 1) * spacing[1]) / 2,
    -((dimensions[2] - 1) * spacing[2]) / 2,
  ];
}

function createProgressiveClipPlane(
  axisIdx: number,
  axisDim: number,
  clipIndex: number,
  direction: 'low_to_high' | 'high_to_low',
  origin: [number, number, number],
  spacing: [number, number, number],
): any | null {
  if (axisDim <= 0) {
    return null;
  }

  const clampedIndex = Math.max(0, Math.min(axisDim - 1, clipIndex));
  const normal: [number, number, number] = [0, 0, 0];
  const planeOrigin: [number, number, number] = [0, 0, 0];

  if (direction === 'high_to_low') {
    const visibleStart = Math.max(axisDim - 1 - clampedIndex, 0);
    if (visibleStart <= 0) {
      return null;
    }

    planeOrigin[axisIdx] =
      origin[axisIdx] + (visibleStart - 0.5) * spacing[axisIdx];
    normal[axisIdx] = 1;
    return vtkPlane.newInstance({ normal, origin: planeOrigin });
  }

  if (clampedIndex >= axisDim - 1) {
    return null;
  }

  planeOrigin[axisIdx] =
    origin[axisIdx] + (clampedIndex + 0.5) * spacing[axisIdx];
  normal[axisIdx] = -1;
  return vtkPlane.newInstance({ normal, origin: planeOrigin });
}

function resetCameraToVolume(
  renderer: any,
  imageData: any,
  container: HTMLDivElement,
  scanView: boolean,
): void {
  const bounds = imageData.getBounds() as [number, number, number, number, number, number];
  const center: [number, number, number] = [
    (bounds[0] + bounds[1]) / 2,
    (bounds[2] + bounds[3]) / 2,
    (bounds[4] + bounds[5]) / 2,
  ];
  const sizeX = Math.max(bounds[1] - bounds[0], 1e-3);
  const sizeY = Math.max(bounds[3] - bounds[2], 1e-3);
  const sizeZ = Math.max(bounds[5] - bounds[4], 1e-3);
  const radius = 0.5 * Math.sqrt(sizeX ** 2 + sizeY ** 2 + sizeZ ** 2);
  const { width, height } = container.getBoundingClientRect();
  const aspect = Math.max(width, 1) / Math.max(height, 1);

  const camera = renderer.getActiveCamera();
  camera.setFocalPoint(center[0], center[1], center[2]);
  camera.setViewAngle(30);

  const viewAngleRad = (30 * Math.PI) / 180;
  const fitHeight = radius / Math.tan(viewAngleRad / 2);
  const fitWidth = (radius * aspect) / Math.tan(viewAngleRad / 2);
  const distance = Math.max(fitHeight, fitWidth) * 1.15;
  camera.setPosition(center[0], center[1] + distance, center[2]);
  camera.setViewUp(0, 0, scanView ? -1 : 1);
  renderer.resetCameraClippingRange(bounds);
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export function VolumeRenderer({
  mode = 'synthetic',
  seriesId,
  opacityPreset: initialPreset = 'ct-soft-tissue',
  showControls = false,
  segmentationMask,
  segmentationLabels,
  syntheticData,
  syntheticDims,
  syntheticSpacing,
  syntheticClipIndex,
  syntheticClipDirection = 'low_to_high',
  syntheticScanAxis = 'z',
  scanView = false,
  scanDirection = 'head_to_feet',
  lesionMeshes,
}: VolumeRendererProps) {
  // ---- DOM ref ----
  const containerRef = useRef<HTMLDivElement>(null);

  // ---- VTK object handles (stored in ref to avoid re-render storms) ----
  const vtkRef = useRef<{
    renderWindow: any;
    renderer: any;
    openglRW: any;
    interactor: any;
    volume?: any;
    mapper?: any;
    imageData?: any;
    colorTransfer?: any;
    piecewiseFunc?: any;
  } | null>(null);

  const segRef = useRef<Map<number, OrganSurfaceRef>>(new Map());

  // ---- Mesh actor refs (lesion mesh lifecycle management) ----
  const meshActorRef = useRef<LesionActorResult[]>([]);

  // ---- UI state ----
  const [isReady, setIsReady] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [activePreset, setActivePreset] = useState<PresetName>(initialPreset);
  const [clip, setClip] = useState<ClipState>({ x: 0, y: 0, z: 0 });
  const [segmentationOpacity, setSegmentationOpacity] = useState(DEFAULT_SEG_OPACITY);
  const [hiddenSegmentationLabels, setHiddenSegmentationLabels] = useState<Set<number>>(new Set());
  const [selectedSegmentationLabel, setSelectedSegmentationLabel] = useState<number | null>(null);

  const applyActiveClippingPlanes = useCallback((imageData: any, targetMappers: any[]) => {
    if (!imageData || targetMappers.length === 0) return;

    const activePlanes: any[] = [];
    const bounds = imageData.getBounds();

    if (clip.x > 0) {
      const xPos = bounds[0] + clip.x * (bounds[1] - bounds[0]);
      activePlanes.push(
        vtkPlane.newInstance({ normal: [1, 0, 0], origin: [xPos, 0, 0] }),
      );
    }

    if (clip.y > 0) {
      const yPos = bounds[2] + clip.y * (bounds[3] - bounds[2]);
      activePlanes.push(
        vtkPlane.newInstance({ normal: [0, 1, 0], origin: [0, yPos, 0] }),
      );
    }

    if (clip.z > 0) {
      const zPos = bounds[4] + clip.z * (bounds[5] - bounds[4]);
      activePlanes.push(
        vtkPlane.newInstance({ normal: [0, 0, 1], origin: [0, 0, zPos] }),
      );
    }

    if (
      syntheticClipIndex !== undefined &&
      syntheticClipIndex >= 0 &&
      syntheticScanAxis
    ) {
      const dims = imageData.getDimensions();
      const spacing = imageData.getSpacing();
      const origin = imageData.getOrigin();
      const axisIdx = syntheticScanAxis === 'x' ? 0
        : syntheticScanAxis === 'y' ? 1 : 2;
      const axisDim = dims[axisIdx];

      const progressivePlane = createProgressiveClipPlane(
        axisIdx,
        axisDim,
        syntheticClipIndex,
        syntheticClipDirection,
        origin,
        spacing,
      );
      if (progressivePlane) {
        activePlanes.push(progressivePlane);
      }
    }

    for (const mapper of targetMappers) {
      if (!mapper) continue;
      mapper.removeAllClippingPlanes();
      for (const plane of activePlanes) {
        mapper.addClippingPlane(plane);
      }
    }
  }, [clip, syntheticClipDirection, syntheticClipIndex, syntheticScanAxis]);

  const applyCtVolumeLook = useCallback((volume: any, mapper: any) => {
    if (!volume || !mapper) return;

    const property = volume.getProperty();
    property.setInterpolationTypeToLinear();
    property.setIndependentComponents(true);
    property.setUseGradientOpacity(0, false);
    property.setShade(true);
    property.setAmbient(0.1);
    property.setDiffuse(0.7);
    property.setSpecular(0.2);
    property.setSpecularPower(10.0);
    if (typeof property.setScalarOpacityUnitDistance === 'function') {
      property.setScalarOpacityUnitDistance(0, 1.0);
    }
    if (typeof mapper.setSampleDistance === 'function' && syntheticSpacing) {
      const minSpacing = Math.min(syntheticSpacing[0], syntheticSpacing[1], syntheticSpacing[2]);
      mapper.setSampleDistance(Math.max(minSpacing * 0.8, 0.25));
    }
  }, [syntheticSpacing]);

  // ------------------------------------------------------------------
  // 1. Initialise vtk.js pipeline
  //    — synthetic: runs once on mount
  //    — dicom: re-runs when seriesId changes
  // ------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    let cancelled = false;

    const init = async () => {
      setIsLoading(true);
      setLoadError(null);
      setClip({ x: 0, y: 0, z: 0 });

      try {
        // ------ mesh mode: standalone lesion preview (no CT volume) ------
        if (mode === 'mesh') {
          if (cancelled) return;

          const rect = container.getBoundingClientRect();
          console.log('[VolumeRenderer] mesh init: container rect', {
            w: rect.width,
            h: rect.height,
            top: rect.top,
            left: rect.left,
          });

          const renderWindow = vtkRenderWindow.newInstance();
          const renderer = vtkRenderer.newInstance();
          renderer.setBackground(0.08, 0.08, 0.12);
          renderWindow.addRenderer(renderer);

          const openglRW = vtkOpenGLRenderWindow.newInstance();
          openglRW.setContainer(container);
          const { width, height } = rect;
          openglRW.setSize(Math.max(width, 1), Math.max(height, 1));
          renderWindow.addView(openglRW);

          // Debug: check created canvas
          const debugCanvas = openglRW.getCanvas();
          console.log('[VolumeRenderer] canvas after creation:', {
            exists: !!debugCanvas,
            tag: debugCanvas?.tagName,
            width: debugCanvas?.width,
            height: debugCanvas?.height,
            parentTag: debugCanvas?.parentElement?.tagName,
          });

          const interactor = vtkRenderWindowInteractor.newInstance();
          interactor.setView(openglRW);
          interactor.initialize();
          interactor.setInteractorStyle(vtkInteractorStyleTrackballCamera.newInstance());
          interactor.bindEvents(container);

          renderWindow.render();

          vtkRef.current = { renderWindow, renderer, openglRW, interactor };

          if (!cancelled) {
            setIsReady(true);
            setIsLoading(false);
          }
          return; // ← skip all volume-related setup
        }

        // ------ acquire volume data ------
        let volData: {
          scalarData: Float32Array;
          dimensions: [number, number, number];
          spacing: [number, number, number];
          origin: [number, number, number];
        };

        if (mode === 'dicom' && seriesId) {
          // ---- load real DICOM series ----
          const dicomVol: DicomVolumeData = await loadDicomVolume(seriesId);
          if (cancelled) return;
          volData = {
            scalarData: dicomVol.scalarData,
            dimensions: dicomVol.dimensions,
            spacing: dicomVol.spacing,
            origin: dicomVol.origin,
          };
        } else {
          // ---- synthetic phantom ----
          if (syntheticData && syntheticDims) {
            // External phantom data provided (e.g. from backend CT phantom API)
            const sp = syntheticSpacing || [1, 1, 1];
            volData = {
              scalarData: syntheticData,
              dimensions: syntheticDims,
              spacing: sp,
              origin: centerVolumeOrigin(syntheticDims, sp),
            };
          } else {
            // Built-in 64³ phantom (legacy / demo)
            volData = {
              scalarData: generateSyntheticVolume(),
              dimensions: [64, 64, 64],
              spacing: [1, 1, 1],
              origin: centerVolumeOrigin([64, 64, 64], [1, 1, 1]),
            };
          }
        }

        if (cancelled) return;

        // ------- vtkImageData -------
        const imageData = vtkImageData.newInstance();
        imageData.setDimensions(volData.dimensions);
        imageData.setSpacing(volData.spacing);
        imageData.setOrigin(volData.origin);

        const dataArray = vtkDataArray.newInstance({
          name: 'Scalars',
          values: volData.scalarData,
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
        applyCtVolumeLook(volume, mapper);

        renderer.addVolume(volume);
        resetCameraToVolume(renderer, imageData, container, scanView);
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
          setLoadError(null);
        }
      } catch (error: any) {
        console.error('[VolumeRenderer] Initialization failed:', error);
        if (!cancelled) {
          setIsLoading(false);
          setLoadError(error.message ?? 'Unknown error loading volume');
        }
      }
    };

    // Defer to next microtask so the container has been laid out
    const timer = setTimeout(init, 0);

    // ------- cleanup -------
    return () => {
      cancelled = true;
      clearTimeout(timer);

      cleanupOrganSurfaces(vtkRef.current?.renderer, segRef.current);

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
  }, [applyCtVolumeLook, mode, scanView, seriesId, syntheticData, syntheticDims, syntheticSpacing]);

  // ------------------------------------------------------------------
  // 2. Handle preset changes (update transfer functions in-place)
  // ------------------------------------------------------------------
  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady) return;

    // Skip if no volume (mesh-only mode — no CT volume to apply presets to)
    if (!state.volume) return;

    const newColor = vtkColorTransferFunction.newInstance();
    const newOpacity = vtkPiecewiseFunction.newInstance();
    applyPreset(newColor, newOpacity, PRESETS[activePreset]);

    state.volume.getProperty().setRGBTransferFunction(0, newColor);
    state.volume.getProperty().setScalarOpacity(0, newOpacity);
    applyCtVolumeLook(state.volume, state.mapper);

    // Replace stored refs so we don't leak old TF objects
    state.colorTransfer = newColor;
    state.piecewiseFunc = newOpacity;

    state.renderWindow.render();
  }, [activePreset, applyCtVolumeLook, isReady]);

  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady || !state.volume || !state.mapper) return;

    applyCtVolumeLook(state.volume, state.mapper);
    state.renderWindow.render();
  }, [applyCtVolumeLook, isReady]);

  // ------------------------------------------------------------------
  // 3. Clipping plane management — user clips + progressive scan clip
  // ------------------------------------------------------------------
  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady) return;

    const { imageData, renderWindow } = state;
    if (!imageData || !renderWindow) return;

    applyActiveClippingPlanes(
      imageData,
      [state.mapper, ...Array.from(segRef.current.values()).map((item) => item.mapper)].filter(Boolean),
    );
    renderWindow.render();
  }, [applyActiveClippingPlanes, isReady]);

  // ------------------------------------------------------------------
  // 4. Segmentation overlay: isolated per-label surface actors.
  // ------------------------------------------------------------------
  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady) return;

    const { renderer, renderWindow, imageData: ctImageData } = state;

    cleanupOrganSurfaces(renderer, segRef.current);

    if (!segmentationMask || !segmentationLabels || segmentationLabels.length === 0) {
      renderWindow.render();
      return;
    }

    const mask = segmentationMask;
    const labels = segmentationLabels;

    try {
      // Get CT volume dimensions
      const ctDims = ctImageData.getDimensions();
      const spacing = ctImageData.getSpacing();
      const origin = ctImageData.getOrigin();

      const ctVoxelCount = ctDims[0] * ctDims[1] * ctDims[2];
      const maskVoxelCount = mask.length;

      // Validate dimension match — critical for correct rendering
      if (maskVoxelCount !== ctVoxelCount) {
        console.error(
          '[VolumeRenderer] Dimension mismatch! CT=%d voxels, mask=%d voxels. Skipping overlay.',
          ctVoxelCount, maskVoxelCount,
        );
        renderWindow.render();
        return;
      }

      for (const label of labels) {
        if (label.index <= 0) continue;

        const organMask = new Uint8Array(maskVoxelCount);
        let organVoxelCount = 0;
        for (let i = 0; i < mask.length; i++) {
          if (Math.round(mask[i]) === label.index) {
            organMask[i] = 1;
            organVoxelCount += 1;
          }
        }
        if (organVoxelCount < MIN_ORGAN_SURFACE_VOXELS) continue;

        const segImageData = vtkImageData.newInstance();
        segImageData.setDimensions(ctDims);
        segImageData.setSpacing(spacing);
        segImageData.setOrigin(origin);
        segImageData.getPointData().setScalars(vtkDataArray.newInstance({
          name: `Segmentation-${label.index}`,
          values: organMask,
          numberOfComponents: 1,
        }));

      const marchingCubes = vtkImageMarchingCubes.newInstance({
        contourValue: 0.5,
        computeNormals: false,
        mergePoints: true,
      });
      marchingCubes.setInputData(segImageData);
      marchingCubes.update();

      const smoother = vtkWindowedSincPolyDataFilter.newInstance({
        numberOfIterations: SURFACE_SMOOTHING_ITERATIONS,
        passBand: SURFACE_SMOOTHING_PASS_BAND,
        featureAngle: 100,
        edgeAngle: 25,
        featureEdgeSmoothing: 0,
        boundarySmoothing: 0,
        normalizeCoordinates: 1,
      });
      smoother.setInputData(marchingCubes.getOutputData());
      smoother.update();

      const normals = vtkPolyDataNormals.newInstance({
        computePointNormals: true,
        computeCellNormals: false,
      });
      normals.setInputData(smoother.getOutputData());
      normals.update();

      const segMapper = vtkMapper.newInstance();
      segMapper.setInputData(normals.getOutputData());
      segMapper.setScalarVisibility(false);

      const segActor = vtkActor.newInstance();
      segActor.setMapper(segMapper);
      const segProp = segActor.getProperty();
      segProp.setColor(label.color[0] / 255, label.color[1] / 255, label.color[2] / 255);
      segProp.setOpacity(label.opacity ?? segmentationOpacity);
      segProp.setAmbient(0.28);
      segProp.setDiffuse(0.62);
      segProp.setSpecular(0.18);
      segProp.setSpecularPower(22.0);
      if (typeof segProp.setInterpolationToPhong === 'function') {
        segProp.setInterpolationToPhong();
      }

      renderer.addActor(segActor);
      applyActiveClippingPlanes(ctImageData, [segMapper]);
      segRef.current.set(label.index, {
        labelIndex: label.index,
        actor: segActor,
        mapper: segMapper,
        imageData: segImageData,
        marchingCubes,
        smoother,
        normals,
      });

      console.info('[VolumeRenderer] Organ surface %d registered (%d voxels)', label.index, organVoxelCount);
      }
      updateOrganSurfaceAppearance(
        segRef.current,
        labels,
        hiddenSegmentationLabels,
        selectedSegmentationLabel,
        segmentationOpacity,
      );
      renderWindow.render();
      return;
      /*

      // Determine the scalar range from the data
      let segMin = Infinity;
      let segMax = -Infinity;
      for (let i = 0; i < segmentationMask.length; i++) {
        const v = segmentationMask[i];
        if (v < segMin) segMin = v;
        if (v > segMax) segMax = v;
      }
      console.info('[VolumeRenderer] Segmentation scalar range: %d – %d', segMin, segMax);

      // ------- color / opacity transfer functions -------
      const segColor = vtkColorTransferFunction.newInstance();
      const segOpacity = vtkPiecewiseFunction.newInstance();

      // Background (0) — fully transparent
      segOpacity.addPoint(-0.5, 0.0);
      segOpacity.addPoint(0.0, 0.0);
      segOpacity.addPoint(0.5, 0.0);

      // Each label gets its color and partial opacity.
      // Use narrow bands around integer label values so that
      // interpolation between labels stays transparent.
      for (const label of segmentationLabels) {
        const [r, g, b] = label.color;
        const idx = label.index;
        if (idx === 0) continue; // background handled above

        segColor.addRGBPoint(idx, r / 255, g / 255, b / 255);

        // Opacity: transparent → visible → transparent
        segOpacity.addPoint(idx - LABEL_HALF_BAND, 0.0);
        segOpacity.addPoint(idx, segmentationOpacity);  // peak opacity
        segOpacity.addPoint(idx + LABEL_HALF_BAND, 0.0);
      }

      // ------- segmentation volume actor -------
      const segMapper = vtkVolumeMapper.newInstance();
      segMapper.setInputData(segImageData);
      segMapper.setBlendModeToComposite();

      // Critical: tell the mapper the exact data range so the transfer
      // function is sampled correctly.
      if (typeof segMapper.setSampleDistance === 'function') {
        // Use the smallest spacing dimension as a reasonable sample distance
        const minSpacing = Math.min(spacing[0], spacing[1], spacing[2]);
        segMapper.setSampleDistance(minSpacing * 0.5);
      }

      const segVolume = vtkVolume.newInstance();
      segVolume.setMapper(segMapper);

      // Higher render priority → rendered later → on top of CT
      if (typeof segVolume.getProperty === 'function') {
        // vtk.js uses Visibility, not RenderPriority for ordering.
        // Setting a higher "layer" via mapper ensures the segmentation
        // is sampled after the CT in the ray-casting pass.
      }

      const segProp = segVolume.getProperty();
      segProp.setRGBTransferFunction(0, segColor);
      segProp.setScalarOpacity(0, segOpacity);
      segProp.setInterpolationTypeToNearest(); // nearest-neighbor for labels
      segProp.setShade(false);                 // no shading for labels
      segProp.setIndependentComponents(true);

      renderer.addVolume(segVolume);
      applyActiveClippingPlanes(ctImageData, [segMapper]);
      renderWindow.render();

      segRef.current = {
        volume: segVolume,
        mapper: segMapper,
        imageData: segImageData,
        colorTransfer: segColor,
        piecewiseFunc: segOpacity,
      };

      console.info('[VolumeRenderer] Segmentation overlay registered (labels=%d)', segmentationLabels.length);
      */
    } catch (err) {
      console.error('[VolumeRenderer] Segmentation overlay failed:', err);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applyActiveClippingPlanes, isReady, segmentationLabels, segmentationMask]);

  // ------------------------------------------------------------------
  // 4b. Segmentation opacity live update — when the slider is dragged,
  //     rebuild only the opacity transfer function (no volume recreation).
  // ------------------------------------------------------------------
  useEffect(() => {
    const state = vtkRef.current;
    if (!state || !isReady || !segmentationLabels || segRef.current.size === 0) return;

    updateOrganSurfaceAppearance(
      segRef.current,
      segmentationLabels,
      hiddenSegmentationLabels,
      selectedSegmentationLabel,
      segmentationOpacity,
    );

    state.renderWindow.render();
  }, [hiddenSegmentationLabels, isReady, segmentationLabels, segmentationOpacity, selectedSegmentationLabel]);

  // ------------------------------------------------------------------
  // 4c. Lesion mesh actors — create/update/cleanup per lesionMeshes prop
  //      Works in all modes: standalone (mode='mesh') or overlaid on CT
  //      (mode='synthetic'|'dicom').
  // ------------------------------------------------------------------
  useEffect(() => {
    const renderer = vtkRef.current?.renderer;
    const renderWindow = vtkRef.current?.renderWindow;
    if (!renderer || !renderWindow || !isReady) return;

    console.log('[VolumeRenderer] lesion effect triggered', {
      lesionMeshes: lesionMeshes?.map(m => ({ id: m.id, v: m.vertices?.length, f: m.faces?.length })),
      mode,
      hasActorRef: meshActorRef.current.length,
    });

    // Cleanup previous mesh actors (remove from scene + GPU memory)
    for (const item of meshActorRef.current) {
      renderer.removeActor(item.actor);
      try { item.actor.delete(); } catch (_) { /* noop */ }
      try { item.mapper.delete(); } catch (_) { /* noop */ }
      try { item.polyData.delete(); } catch (_) { /* noop */ }
    }
    meshActorRef.current = [];

    // No meshes to show — just re-render and exit
    if (!lesionMeshes || lesionMeshes.length === 0) {
      renderWindow.render();
      return;
    }

    // Create actors for every visible mesh
    const actors: LesionActorResult[] = [];
    for (const mesh of lesionMeshes) {
      if (mesh.visible === false) continue; // skip hidden

      try {
        const result = createLesionActor(mesh.vertices, mesh.faces, mesh.normals, {
          opacity: mesh.opacity ?? 1.0,
          color: mesh.color ?? [1, 0.3, 0.3],
          visible: true,
        });
        const bounds = result.actor.getBounds();
        console.log('[VolumeRenderer] created actor', mesh.id, {
          bounds,
          nVertices: mesh.vertices.length,
          nFaces: mesh.faces.length,
        });
        renderer.addActor(result.actor);
        actors.push(result);
      } catch (err) {
        console.error('[VolumeRenderer] Failed to create lesion actor for mesh', mesh.id, err);
      }
    }
    meshActorRef.current = actors;

    // Standalone mesh mode: frame camera to the combined mesh bounding box
    if (mode === 'mesh' && actors.length > 0 && containerRef.current) {
      resetCameraToMesh(renderer, actors, containerRef.current);
      const cam = renderer.getActiveCamera();
      console.log('[VolumeRenderer] camera position after reset:', {
        pos: cam.getPosition(),
        fp: cam.getFocalPoint(),
      });
    }

    renderWindow.render();
    // Check canvas dimensions
    if (mode === 'mesh') {
      const gl = vtkRef.current?.openglRW?.getCanvas?.();
      if (gl) {
        console.log('[VolumeRenderer] canvas size:', gl.width, 'x', gl.height);
      }
      // Check WebGL context
      try {
        const canvas = vtkRef.current?.openglRW?.getCanvas?.();
        if (canvas) {
          const ctx = canvas.getContext('webgl2') || canvas.getContext('webgl');
          console.log('[VolumeRenderer] WebGL context:', !!ctx, 'canvas parent:', canvas.parentElement?.className);
          console.log('[VolumeRenderer] canvas CSS:', getComputedStyle(canvas).width, getComputedStyle(canvas).height);
        }
      } catch(e) {
        console.warn('[VolumeRenderer] WebGL check error:', e);
      }
    }
  }, [lesionMeshes, isReady, mode]);

  // ------------------------------------------------------------------
  // 5. Handle container resize
  // ------------------------------------------------------------------
  useEffect(() => {
    const container = containerRef.current;
    const state = vtkRef.current;
    if (!container || !state || !isReady) return;

    const observer = new ResizeObserver(() => {
      const { width, height } = container.getBoundingClientRect();
      if (width > 0 && height > 0) {
        state.openglRW.setSize(width, height);

        if (mode === 'mesh' && meshActorRef.current.length > 0) {
          resetCameraToMesh(state.renderer, meshActorRef.current, container);
        } else if (state.imageData) {
          resetCameraToVolume(
            state.renderer,
            state.imageData,
            container,
            scanView,
          );
        }

        state.renderWindow.render();
      }
    });

    observer.observe(container);
    return () => observer.disconnect();
  }, [isReady, scanView, mode]);

  // ------------------------------------------------------------------
  // 5. Preset button handler
  // ------------------------------------------------------------------
  const handlePresetChange = useCallback((name: PresetName) => {
    setActivePreset(name);
  }, []);

  const toggleSegmentationLabel = useCallback((labelIndex: number) => {
    setHiddenSegmentationLabels((current) => {
      const next = new Set(current);
      if (next.has(labelIndex)) next.delete(labelIndex);
      else next.add(labelIndex);
      return next;
    });
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
              {mode === 'dicom' ? 'Loading DICOM volume...' : 'Loading volume...'}
            </span>
          </div>
        </div>
      )}

      {/* Error overlay */}
      {loadError && !isLoading && (
        <div className="absolute inset-0 z-20 flex items-center justify-center bg-black/80">
          <div className="max-w-sm text-center">
            <p className="mb-1 text-sm font-medium text-red-400">
              Failed to load volume
            </p>
            <p className="text-xs text-white/50">{loadError}</p>
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
        <div className="absolute bottom-2 right-2 z-10 flex flex-col gap-1.5 items-end">
          {/* Preset buttons */}
          <div className="flex gap-1">
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

          {/* Segmentation opacity slider — only when overlay is active */}
          {segmentationMask && segmentationLabels && segmentationLabels.length > 0 && (
            <div className="flex max-h-52 min-w-[220px] flex-col gap-1 overflow-y-auto rounded bg-black/60 px-2 py-1.5">
              <SegOpacitySlider
                value={segmentationOpacity}
                onChange={setSegmentationOpacity}
              />
              <div className="mt-1 border-t border-white/10 pt-1">
                {segmentationLabels.filter((label) => label.index > 0).map((label) => {
                  const visible = !hiddenSegmentationLabels.has(label.index) && (label.visible ?? true);
                  const selected = selectedSegmentationLabel === label.index || label.selected === true;
                  return (
                    <div key={label.index} className="flex items-center gap-1 text-[10px] text-white/75">
                      <input
                        type="checkbox"
                        checked={visible}
                        onChange={() => toggleSegmentationLabel(label.index)}
                        aria-label={`Toggle ${label.name}`}
                      />
                      <button
                        type="button"
                        className={`flex min-w-0 flex-1 items-center gap-1 rounded px-1 py-0.5 text-left ${selected ? 'bg-white/20 text-white' : 'hover:bg-white/10'}`}
                        onClick={() => setSelectedSegmentationLabel((current) => current === label.index ? null : label.index)}
                      >
                        <span
                          className="h-2 w-2 shrink-0 rounded-full"
                          style={{ backgroundColor: `rgb(${label.color.join(',')})` }}
                        />
                        <span className="truncate">{label.name}</span>
                      </button>
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {/* Clipping plane sliders */}
          <div className="flex flex-col gap-0.5 rounded bg-black/60 px-2 py-1.5 min-w-[220px]">
            <ClipSlider
              label="X"
              value={clip.x}
              onChange={(v) => setClip((prev) => ({ ...prev, x: v }))}
            />
            <ClipSlider
              label="Y"
              value={clip.y}
              onChange={(v) => setClip((prev) => ({ ...prev, y: v }))}
            />
            <ClipSlider
              label="Z"
              value={clip.z}
              onChange={(v) => setClip((prev) => ({ ...prev, z: v }))}
            />
          </div>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Frame the camera to encompass all mesh actors.
 * Computes the combined axis-aligned bounding box across all actors
 * and positions the camera so the full bounding box is visible.
 */
function resetCameraToMesh(
  renderer: any,
  actors: LesionActorResult[],
  container: HTMLDivElement,
): void {
  // Compute combined bounding box
  let bounds: [number, number, number, number, number, number] | null = null;
  for (const { actor } of actors) {
    const b = actor.getBounds();
    if (!b) continue;
    if (!bounds) {
      bounds = [b[0], b[1], b[2], b[3], b[4], b[5]];
    } else {
      bounds[0] = Math.min(bounds[0], b[0]);
      bounds[1] = Math.max(bounds[1], b[1]);
      bounds[2] = Math.min(bounds[2], b[2]);
      bounds[3] = Math.max(bounds[3], b[3]);
      bounds[4] = Math.min(bounds[4], b[4]);
      bounds[5] = Math.max(bounds[5], b[5]);
    }
  }
  if (!bounds) return;

  const center: [number, number, number] = [
    (bounds[0] + bounds[1]) / 2,
    (bounds[2] + bounds[3]) / 2,
    (bounds[4] + bounds[5]) / 2,
  ];

  const diag = Math.sqrt(
    (bounds[1] - bounds[0]) ** 2 +
    (bounds[3] - bounds[2]) ** 2 +
    (bounds[5] - bounds[4]) ** 2,
  );

  const { width, height } = container.getBoundingClientRect();
  const aspect = Math.max(width, 1) / Math.max(height, 1);

  const camera = renderer.getActiveCamera();
  camera.setFocalPoint(center[0], center[1], center[2]);
  camera.setViewAngle(30);

  const viewAngleRad = (30 * Math.PI) / 180;
  const fitHeight = diag / 2 / Math.tan(viewAngleRad / 2);
  const fitWidth = (diag / 2 * aspect) / Math.tan(viewAngleRad / 2);
  const distance = Math.max(fitHeight, fitWidth) * 1.15;

  camera.setPosition(center[0], center[1] + distance, center[2]);
  camera.setViewUp(0, 0, -1);

  renderer.resetCameraClippingRange(bounds);
}

function cleanupOrganSurfaces(renderer: any | undefined, surfaces: Map<number, OrganSurfaceRef>): void {
  for (const surface of surfaces.values()) {
    try { renderer?.removeActor(surface.actor); } catch (_) { /* ignore */ }
    try { surface.actor.delete(); } catch (_) { /* ignore */ }
    try { surface.mapper.delete(); } catch (_) { /* ignore */ }
    try { surface.imageData.delete(); } catch (_) { /* ignore */ }
    try { surface.marchingCubes.delete(); } catch (_) { /* ignore */ }
    try { surface.smoother.delete(); } catch (_) { /* ignore */ }
    try { surface.normals.delete(); } catch (_) { /* ignore */ }
  }
  surfaces.clear();
}

function updateOrganSurfaceAppearance(
  surfaces: Map<number, OrganSurfaceRef>,
  labels: SegmentationLabelDef[],
  hiddenLabels: Set<number>,
  selectedLabel: number | null,
  defaultOpacity: number,
): void {
  for (const label of labels) {
    const surface = surfaces.get(label.index);
    if (!surface) continue;

    const [r, g, b] = label.color;
    const selected = label.selected ?? selectedLabel === label.index;
    const visible = (label.visible ?? true) && !hiddenLabels.has(label.index);
    const opacity = visible
      ? Math.min(1, (label.opacity ?? defaultOpacity) + (selected ? SELECTED_OPACITY_BOOST : 0))
      : 0;
    const prop = surface.actor.getProperty();

    prop.setColor(r / 255, g / 255, b / 255);
    prop.setOpacity(opacity);
    surface.actor.setVisibility(visible || opacity > 0);
  }
}

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

/** Small slider for a single clipping axis. */
function ClipSlider({
  label,
  value,
  onChange,
}: {
  label: string;
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-3 text-right text-[10px] font-medium text-white/50">
        {label}
      </span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="h-1 flex-1 cursor-pointer accent-primary"
      />
      <span className="w-8 text-right text-[10px] tabular-nums text-white/50">
        {Math.round(value * 100)}%
      </span>
    </div>
  );
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

/** Slider for adjusting segmentation overlay opacity. */
function SegOpacitySlider({
  value,
  onChange,
}: {
  value: number;
  onChange: (v: number) => void;
}) {
  return (
    <div className="flex items-center gap-1.5">
      <span className="w-auto text-[10px] font-medium text-white/70 whitespace-nowrap">
        Opacity
      </span>
      <input
        type="range"
        min={0}
        max={1}
        step={0.01}
        value={value}
        onChange={(e) => onChange(parseFloat(e.target.value))}
        className="h-1 flex-1 cursor-pointer accent-primary"
      />
      <span className="w-8 text-right text-[10px] tabular-nums text-white/50">
        {Math.round(value * 100)}%
      </span>
    </div>
  );
}
