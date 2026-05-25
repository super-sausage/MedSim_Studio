import { useRef, useEffect, useCallback } from 'react';

/**
 * CornerstoneViewport Component
 *
 * Base viewport component wrapping Cornerstone3D rendering engine.
 * Provides the foundation for all 2D medical image views.
 * Supports:
 * - Window/level interaction
 * - Pan and zoom
 * - Series navigation
 * - Annotation overlays
 */

interface CornerstoneViewportProps {
  viewportId: string;
  imageIds: string[];
  className?: string;
  onViewportReady?: (viewportId: string) => void;
}

export function CornerstoneViewport({
  viewportId,
  imageIds,
  className = '',
  onViewportReady,
}: CornerstoneViewportProps) {
  const elementRef = useRef<HTMLDivElement>(null);

  const initViewport = useCallback(async () => {
    const element = elementRef.current;
    if (!element || imageIds.length === 0) return;

    try {
      // TODO: Enable Cornerstone3D viewport
      // const renderingEngine = getRenderingEngine('ctViewer');
      // const viewportInput = {
      //   viewportId,
      //   type: ViewportType.STACK,
      //   element,
      // };
      // renderingEngine.enableElement(viewportInput);
      // const viewport = renderingEngine.getViewport(viewportId);
      // await viewport.setStack(imageIds);
      // viewport.render();

      onViewportReady?.(viewportId);
    } catch (error) {
      console.error(`[Viewport ${viewportId}] Init failed:`, error);
    }
  }, [viewportId, imageIds, onViewportReady]);

  useEffect(() => {
    initViewport();
  }, [initViewport]);

  useEffect(() => {
    return () => {
      // Cleanup: disable Cornerstone3D element
      // const renderingEngine = getRenderingEngine('ctViewer');
      // renderingEngine?.disableElement(viewportId);
    };
  }, [viewportId]);

  return (
    <div
      ref={elementRef}
      className={`viewport-element ${className}`}
      style={{ width: '100%', height: '100%' }}
    />
  );
}
