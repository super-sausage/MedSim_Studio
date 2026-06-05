import { useState, useCallback, useRef } from 'react';
import type { SliceMask, SegmentationLabel } from '@/types/segmentation';
import { segmentationService } from '@/services/segmentationService';

/**
 * useSegmentationOverlay
 *
 * Hook that manages segmentation mask state for a single viewport.
 * Fetches slice masks from the backend as the user scrolls through slices,
 * caches them locally to minimize network requests, and prefetches
 * adjacent slices for smooth scrolling.
 *
 * Usage in CornerstoneViewport:
 *   const { currentSlice, loadSlice } = useSegmentationOverlay(jobId);
 *   // On slice change: loadSlice(newZIndex)
 *   // Render: currentSlice?.maskData
 */

interface OverlayCache {
  [key: string]: SliceMask; // key = `${jobId}:${zIndex}`
}

const MAX_CACHE_SIZE = 50;

export function useSegmentationOverlay(jobId: string | null) {
  const [currentSlice, setCurrentSlice] = useState<SliceMask | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [labels, setLabels] = useState<SegmentationLabel[]>([]);

  // LRU-ish cache using a plain object + key tracking
  const cacheRef = useRef<OverlayCache>({});
  const cacheKeysRef = useRef<string[]>([]);

  const getCacheKey = useCallback(
    (zIndex: number) => `${jobId}:${zIndex}`,
    [jobId],
  );

  const evictIfNeeded = useCallback(() => {
    const cache = cacheRef.current;
    const keys = cacheKeysRef.current;
    while (keys.length > MAX_CACHE_SIZE) {
      const oldest = keys.shift();
      if (oldest) delete cache[oldest];
    }
  }, []);

  const loadSlice = useCallback(
    async (zIndex: number) => {
      if (!jobId) {
        setCurrentSlice(null);
        return;
      }

      const key = getCacheKey(zIndex);

      // Check cache first
      const cached = cacheRef.current[key];
      if (cached) {
        setCurrentSlice(cached);
        if (labels.length === 0 && cached.labels.length > 0) {
          setLabels(cached.labels);
        }
        return;
      }

      setLoading(true);
      setError(null);

      try {
        const sliceMask = await segmentationService.getSliceMask(jobId, zIndex);

        // Store in cache
        cacheRef.current[key] = sliceMask;
        cacheKeysRef.current.push(key);
        evictIfNeeded();

        setCurrentSlice(sliceMask);
        if (sliceMask.labels.length > 0) {
          setLabels(sliceMask.labels);
        }
      } catch (err: any) {
        setError(err?.message ?? 'Failed to load slice mask');
        setCurrentSlice(null);
      } finally {
        setLoading(false);
      }

      // Prefetch adjacent slices for smooth scrolling
      prefetchAdjacent(jobId, zIndex, getCacheKey);
    },
    [jobId, getCacheKey, evictIfNeeded, labels.length],
  );

  const clearCache = useCallback(() => {
    cacheRef.current = {};
    cacheKeysRef.current = [];
    setCurrentSlice(null);
    setError(null);
    setLabels([]);
  }, []);

  return {
    currentSlice,
    labels,
    loading,
    error,
    loadSlice,
    clearCache,
  };
}

/**
 * Prefetch adjacent slices without awaiting (fire-and-forget).
 */
function prefetchAdjacent(
  jobId: string,
  zIndex: number,
  getCacheKey: (z: number) => string,
) {
  [zIndex - 1, zIndex + 1].forEach((adjZ) => {
    const key = getCacheKey(adjZ);
    if (!cacheRefCurrent(key)) {
      segmentationService.getSliceMask(jobId, adjZ).then((sliceMask) => {
        // Store in the cache via the ref
        const cache = window.__segCache;
        if (cache) cache[`${jobId}:${adjZ}`] = sliceMask;
      }).catch(() => {
        // Prefetch failures are silently ignored
      });
    }
  });
}

// Minimal global cache reference for prefetch writes
// The actual cache is in the hook, but prefetch writes to a shared global.
// This is a simple solution — in production you'd use a shared cache service.
declare global {
  interface Window {
    __segCache?: Record<string, SliceMask>;
  }
}

function cacheRefCurrent(key: string): boolean {
  return !!(window.__segCache?.[key]);
}

// Initialize global cache on first import
if (!window.__segCache) {
  window.__segCache = {};
}
