/**
 * createLesionActor
 *
 * Utility that converts lesion mesh data (vertices + faces + normals)
 * into a vtk.js rendering pipeline:
 *
 *   vertices/faces/normals
 *        ↓
 *   vtkPolyData
 *        ↓
 *   vtkMapper
 *        ↓
 *   vtkActor  ──→  add to vtkRenderer
 *
 * Reuses the existing vtk.js install — no new rendering framework.
 */

import vtkPoints from '@kitware/vtk.js/Common/Core/Points';
import vtkCellArray from '@kitware/vtk.js/Common/Core/CellArray';
import vtkDataArray from '@kitware/vtk.js/Common/Core/DataArray';
import vtkPolyData from '@kitware/vtk.js/Common/DataModel/PolyData';
import vtkMapper from '@kitware/vtk.js/Rendering/Core/Mapper';
import vtkActor from '@kitware/vtk.js/Rendering/Core/Actor';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface LesionActorOptions {
  /** Opacity 0..1 (default 1.0) */
  opacity?: number;
  /** RGB color components 0..1 (default [1, 0.3, 0.3]) */
  color?: [number, number, number];
  /** Visibility (default true) */
  visible?: boolean;
  /** Specular power for surface highlight (default 10) */
  specularPower?: number;
}

export interface LesionActorResult {
  actor: any;
  mapper: any;
  polyData: any;
}

// ---------------------------------------------------------------------------
// Factory
// ---------------------------------------------------------------------------

/**
 * Build a vtkActor from raw triangle mesh data.
 *
 * @param vertices  N×3 array of vertex positions in physical mm
 * @param faces     M×3 array of triangle indices (0-based)
 * @param normals   N×3 array of per-vertex normals
 * @param options   Visual properties (opacity, color, visibility)
 */
export function createLesionActor(
  vertices: number[][],
  faces: number[][],
  normals: number[][],
  options: LesionActorOptions = {},
): LesionActorResult {
  // ── 1. vtkPoints from vertex array ──
  const n = vertices.length;
  const pointsArray = new Float64Array(n * 3);
  for (let i = 0; i < n; i++) {
    pointsArray[i * 3] = vertices[i][0];
    pointsArray[i * 3 + 1] = vertices[i][1];
    pointsArray[i * 3 + 2] = vertices[i][2];
  }
  const points = vtkPoints.newInstance();
  points.setData(pointsArray, 3);

  // ── 2. vtkCellArray from face indices ──
  // Legacy format: [nPts, i0, i1, i2, nPts, j0, j1, j2, ...]
  const m = faces.length;
  const cellArrayData = new Uint32Array(m * 4);
  for (let i = 0; i < m; i++) {
    cellArrayData[i * 4] = 3; // triangle
    cellArrayData[i * 4 + 1] = faces[i][0];
    cellArrayData[i * 4 + 2] = faces[i][1];
    cellArrayData[i * 4 + 3] = faces[i][2];
  }
  const cells = vtkCellArray.newInstance();
  cells.setData(cellArrayData);

  // ── 3. vtkPolyData ──
  const polyData = vtkPolyData.newInstance();
  polyData.setPoints(points);
  polyData.setPolys(cells);

  // ── 4. Normals (optional but recommended for correct lighting) ──
  if (normals && normals.length === n) {
    const normalsArray = new Float32Array(n * 3);
    for (let i = 0; i < n; i++) {
      normalsArray[i * 3] = normals[i][0];
      normalsArray[i * 3 + 1] = normals[i][1];
      normalsArray[i * 3 + 2] = normals[i][2];
    }
    const vtkNormals = vtkDataArray.newInstance({
      name: 'Normals',
      values: normalsArray,
      numberOfComponents: 3,
    });
    polyData.getPointData().setNormals(vtkNormals);
  }

  // ── 5. vtkMapper ──
  const mapper = vtkMapper.newInstance();
  mapper.setInputData(polyData);
  mapper.setScalarVisibility(false); // use actor color, not data scalars

  // ── 6. vtkActor ──
  const actor = vtkActor.newInstance();
  actor.setMapper(mapper);

  const { opacity = 1.0, color = [1, 0.3, 0.3], visible = true, specularPower = 10 } = options;
  const prop = actor.getProperty();
  prop.setColor(color[0], color[1], color[2]);
  prop.setOpacity(opacity);
  prop.setSpecular(opacity > 0.2 ? 0.35 : 0.1);
  prop.setSpecularPower(specularPower);
  prop.setDiffuse(0.7);
  prop.setAmbient(0.3);
  actor.setVisibility(visible);

  return { actor, mapper, polyData };
}
